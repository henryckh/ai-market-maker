"""HTTP API for multi-step bar backtests."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import anyio
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from api.safe_ids import path_under, require_safe_id
from backtest.bars import (
    align_bars_by_min_length,
    fetch_ccxt_ohlcv_bars,
    fetch_ccxt_ohlcv_range,
    fetch_futu_ohlcv_bars,
    fetch_yfinance_ohlcv_bars,
    interval_sec_to_ccxt_timeframe,
    iso_utc_to_ms,
    load_ohlcv_json,
)
from backtest.config import resolve_backtest_config, set_env_from_config
from backtest.exchange_trade_format import normalize_trade_row_for_api
from backtest.loop import MultiStepResult, run_multi_step_backtest
from backtest.ta_warmup import (
    split_warmup_index,
    total_fetch_bars,
    warmup_fetch_since_ms,
)
from backtest.trade_book import read_jsonl_dict_records
from config.runs_paths import runs_dir as _resolved_runs_dir
from strategies.presets import (
    DEFAULT_QUANT_STRATEGY_ID,
    get_preset,
    list_presets,
    merge_preset_quick_request,
)

RUNS_DIR = _resolved_runs_dir()
BACKTESTS_DIR = RUNS_DIR / "backtests"

# Async preset jobs: UI polls GET /backtests/jobs/{run_id} for step progress.
# Jobs run in daemon threads — API restart orphans "running" job.json files.
BACKTEST_JOBS: dict[str, dict[str, Any]] = {}

router = APIRouter(tags=["backtests"])
logger = logging.getLogger(__name__)


def _max_api_steps() -> int:
    return max(20, int(os.environ.get("BACKTEST_API_MAX_STEPS", "5000")))


def _job_stale_sec() -> int:
    """No progress for this long → treat job as dead (LLM hang or killed worker)."""
    return max(60, int(os.environ.get("BACKTEST_JOB_STALE_SEC", "1800")))


def _run_dir(run_id: str) -> Path:
    rid = require_safe_id(str(run_id), name="run_id")
    return path_under(_resolved_runs_dir() / "backtests", rid)


def _job_path(run_id: str) -> Path:
    return _run_dir(run_id) / "job.json"


def _write_job(run_id: str, payload: dict[str, Any]) -> None:
    """Persist job progress so multi-worker servers can poll reliably."""
    try:
        data = dict(payload)
        data["updated_at"] = int(time.time())
        p = _job_path(run_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(p)
        # Keep in-memory copy aligned when this worker owns the job.
        if run_id in BACKTEST_JOBS:
            BACKTEST_JOBS[run_id] = {**BACKTEST_JOBS[run_id], **data}
    except Exception:
        pass


def _job_updated_at(job: dict[str, Any], *, run_id: str) -> int | None:
    raw = job.get("updated_at")
    if isinstance(raw, (int, float)) and raw > 0:
        return int(raw)
    p = _job_path(run_id)
    try:
        if p.is_file():
            return int(p.stat().st_mtime)
    except Exception:
        pass
    return None


def _fail_job(run_id: str, job: dict[str, Any], *, error: str) -> dict[str, Any]:
    failed = {
        "status": "failed",
        "error": error,
        "step": job.get("step"),
        "total_steps": job.get("total_steps"),
        "trade_count": job.get("trade_count"),
        "equity": job.get("equity"),
    }
    BACKTEST_JOBS[run_id] = failed
    _write_job(run_id, failed)
    return failed


def _job_progress_update(
    run_id: str,
    i: int,
    total: int,
    snap: dict[str, Any],
) -> None:
    """Persist UI progress for the scored window only.

    ``i`` is 0-based within the eval window, or ``-1`` while TA warmup runs
    (shown as step 0 / 0%).
    """
    if run_id not in BACKTEST_JOBS:
        return
    step = 0 if int(i) < 0 else int(i) + 1
    BACKTEST_JOBS[run_id].update(
        {
            "status": "running",
            "step": step,
            "total_steps": max(1, int(total)),
            "trade_count": snap.get("trade_count", 0),
            "equity": snap.get("equity"),
            "capital": snap.get("capital"),
            "positions": snap.get("positions", 0),
            "ts": snap.get("ts"),
            "vetoed": snap.get("vetoed"),
            "warmup": bool(snap.get("warmup")),
        }
    )
    _write_job(run_id, dict(BACKTEST_JOBS[run_id]))


def _maybe_fail_stale_job(run_id: str, job: dict[str, Any]) -> dict[str, Any]:
    status = str(job.get("status") or "")
    if status not in ("running", "queued"):
        return job
    updated = _job_updated_at(job, run_id=run_id)
    if updated is None:
        return job
    age = int(time.time()) - updated
    stale_after = _job_stale_sec()
    if age <= stale_after:
        return job
    logger.warning(
        "backtest job stale run_id=%s step=%s/%s age_sec=%s",
        run_id,
        job.get("step"),
        job.get("total_steps"),
        age,
    )
    return _fail_job(
        run_id,
        job,
        error=(
            f"Backtest stalled or interrupted (no progress for {age}s). "
            "This often happens after an API restart mid-run — start a new Research backtest."
        ),
    )


def recover_stale_backtest_jobs(*, reason: str = "api_startup") -> int:
    """Mark orphaned running/queued jobs as failed.

    Daemon threads die on process restart; without this the UI polls forever
    (e.g. stuck at 248/250 bars).
    """
    if not BACKTESTS_DIR.is_dir():
        return 0
    n = 0
    for d in BACKTESTS_DIR.iterdir():
        if not d.is_dir():
            continue
        p = d / "job.json"
        if not p.is_file():
            continue
        try:
            job = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(job, dict):
            continue
        if str(job.get("status") or "") != "running":
            continue
        _fail_job(
            d.name,
            job,
            error=(
                f"Backtest interrupted ({reason}). "
                "The worker was restarted while this job was running — start a new Research backtest."
            ),
        )
        n += 1
    if n:
        logger.info("recovered %s orphaned backtest job(s) reason=%s", n, reason)
    return n


def _jsonl_preview(path: Path, *, limit: int = 20) -> list[dict[str, Any]]:
    return read_jsonl_dict_records(path, limit=limit)


def _read_jsonl_all(path: Path) -> list[dict[str, Any]]:
    return read_jsonl_dict_records(path)


def _downsample_rows(rows: list[dict[str, Any]], max_points: int) -> list[dict[str, Any]]:
    """Evenly sample rows (inclusive ends) so charts stay responsive for long runs."""
    n = len(rows)
    if n <= max_points or max_points < 2:
        return rows
    indices = sorted({int(round(i * (n - 1) / (max_points - 1))) for i in range(max_points)})
    return [rows[i] for i in indices]


def _normalize_equity_point(row: dict[str, Any], idx: int) -> dict[str, Any]:
    """Map engine equity.jsonl fields → UI EquityPoint (step, ts_ms, equity)."""
    ts = row.get("ts_ms", row.get("ts"))
    step = row.get("step", idx)
    try:
        step_i = int(step)
    except Exception:
        step_i = idx
    try:
        ts_i = int(ts) if ts is not None else 0
    except Exception:
        ts_i = 0
    eq = row.get("equity", row.get("capital"))
    try:
        eq_f = float(eq) if eq is not None else 0.0
    except Exception:
        eq_f = 0.0
    out: dict[str, Any] = {"step": step_i, "ts_ms": ts_i, "equity": eq_f}
    if row.get("close") is not None:
        out["close"] = row.get("close")
    if row.get("vetoed") is not None:
        out["vetoed"] = row.get("vetoed")
    pos = row.get("positions")
    if isinstance(pos, (int, float)):
        out["positions"] = int(pos)
    u = row.get("unrealized_pnl")
    if isinstance(u, (int, float)):
        out["unrealized_pnl"] = float(u)
    return out


def _normalize_ohlcv_bar(row: dict[str, Any], idx: int) -> dict[str, Any]:
    """Map bars.json compact keys (ts/o/h/l/c/v) → UI OhlcvBar."""
    ts = row.get("ts_ms", row.get("ts"))
    step = row.get("step", idx)
    try:
        step_i = int(step)
    except Exception:
        step_i = idx
    try:
        ts_i = int(ts) if ts is not None else 0
    except Exception:
        ts_i = 0

    def _f(*keys: str, default: float = 0.0) -> float:
        for k in keys:
            if row.get(k) is not None:
                try:
                    return float(row[k])
                except Exception:
                    continue
        return default

    return {
        "step": step_i,
        "ts_ms": ts_i,
        "open": _f("open", "o"),
        "high": _f("high", "h"),
        "low": _f("low", "l"),
        "close": _f("close", "c"),
        "volume": _f("volume", "v"),
    }


def _evaluation_block(
    *,
    result: MultiStepResult,
    initial_cash: float,
) -> dict[str, Any]:
    final = float(result.final_equity) if result.final_equity is not None else float(initial_cash)
    ret_pct = ((final - initial_cash) / initial_cash * 100.0) if initial_cash else 0.0
    block: dict[str, Any] = {
        "initial_cash": initial_cash,
        "final_equity": final,
        "total_return_pct": round(ret_pct, 4),
        "trade_count": result.trade_count,
        "trades_preview": [
            normalize_trade_row_for_api(r) for r in _jsonl_preview(result.trades_path, limit=15)
        ],
        "note": (
            "Fills are simulated at each bar's close when Risk Guard approves and the portfolio "
            "desk proposes a trade; see paths.trades for the full JSONL ledger."
        ),
    }
    if result.benchmark is not None:
        block["benchmark"] = dict(result.benchmark)
    return block


def _backtest_paths_response(result: MultiStepResult) -> dict[str, Any]:
    return {
        "summary": str(result.summary_path),
        "trades": str(result.trades_path),
        "equity": str(result.equity_path),
        "iterations": str(result.iterations_path) if result.iterations_path else None,
        "events": str(result.events_path),
    }


class QuickBacktestRequest(BaseModel):
    ticker: str = Field("BTC/USDT", min_length=3)
    symbols: list[str] | None = Field(
        None,
        description=(
            'Optional multi-symbol universe (e.g. ["BTC/USDT","ETH/USDT","SOL/USDT"]). '
            "When 2+ symbols are set, OHLCV is loaded for each and the book is multi-asset; "
            "ticker remains the primary/benchmark label (defaults to symbols[0])."
        ),
    )
    n_bars: int = Field(200, ge=20, le=100_000)
    interval_sec: int = Field(
        300,
        ge=60,
        le=86_400,
        description="Bar size in seconds (e.g. 300 = 5m).",
    )
    initial_cash: float = Field(100_000.0, gt=0)
    fee_bps: float = Field(10.0, ge=0, le=500)
    max_steps: int | None = Field(
        None,
        ge=1,
        description="Optional cap on bars processed (subject to server cap).",
    )
    exchange_id: str = Field(
        "binance",
        description=(
            'Data source: CCXT id (e.g. "binance"), "yahoo" for Yahoo Finance '
            '(equities/crypto, no API key), or "futu" for Futu OpenD (optional).'
        ),
    )
    since_iso: str | None = Field(
        None,
        description="ccxt_range: ISO date/datetime (UTC) for range start, e.g. 2023-01-01.",
    )
    until_iso: str | None = Field(
        None,
        description="ccxt_range: ISO date/datetime (UTC) for range end, e.g. 2024-01-01.",
    )
    # Optional strategy overrides from the builder: desk weights, arbitrator mode.
    deploy: dict[str, Any] | None = Field(
        None,
        description=(
            "Inline deploy from aimm-web builder. Prefer full agents+execution "
            "(deploy.active.json shape); legacy profile_weights+arbitrator_mode still work. "
            "Does NOT write deploy.active.json."
        ),
    )


class DemoBacktestRequest(BaseModel):
    """README-style demo defaults: multi-symbol, aligned bars, single portfolio run."""

    symbols: str = Field(
        "BTC/USDT,ETH/USDT,SOL/USDT",
        min_length=3,
        description="Comma-separated symbols, min 2. CCXT pairs for binance (e.g. BTC/USDT); "
        "Futu codes for exchange_id=futu (e.g. HK.00700,HK.09988).",
    )
    steps: int = Field(100, ge=20, le=20_000, description="Candles to fetch and replay.")
    interval_sec: int = Field(
        86_400,
        ge=60,
        le=86_400,
        description="Bar size in seconds (default: 1d).",
    )
    exchange_id: str = Field(
        "binance",
        description='CCXT exchange id, "yahoo", or "futu" for OpenD multi-symbol runs.',
    )
    initial_cash: float = Field(100_000.0, gt=0)
    fee_bps: float = Field(10.0, ge=0, le=500)


class StrategyBacktestRequest(BaseModel):
    """Multi-tenant backtest: inline deploy JSON, never writes config/deploy.active.json."""

    user_id: str = ""
    strategy_id: str = ""
    ticker: str = Field("BTC/USDT", min_length=3)
    symbols: str = ""
    n_bars: int = Field(180, ge=20, le=100_000)
    interval_sec: int = Field(3600, ge=60, le=86_400)
    initial_cash: float = Field(100_000.0, gt=0)
    fee_bps: float = Field(5.0, ge=0, le=500)
    exchange_id: str = "binance"
    since_iso: str | None = None
    until_iso: str | None = None
    deploy: dict[str, Any]


def _until_ms_inclusive(until_iso: str) -> int:
    """UTC ms end bound; date-only values include the full calendar day."""
    raw = (until_iso or "").strip()
    until_ms = iso_utc_to_ms(raw)
    if "T" not in raw and len(raw) <= 10:
        return until_ms + 86_400_000 - 1
    return until_ms


def _merge_inline_deploy_overrides(cfg: dict[str, Any], deploy: dict[str, Any]) -> None:
    """Merge aimm-web / tenant inline deploy into resolved backtest cfg in-place.

    Preferred shape (matches config/deploy.*.json)::

        { "agents": { "<desk>": {"weight", "enabled", "llm_enabled"}, ... },
          "execution": {...}, "decision_threshold": {...}, "arbitrator_mode": "..." }

    Legacy shape (profile_weights + arbitrator_mode only) still accepted.
    """
    agents = deploy.get("agents")
    if isinstance(agents, dict) and agents:
        cfg["agents"] = dict(agents)
        weights: dict[str, float] = {}
        for name, meta in agents.items():
            if not isinstance(meta, dict) or meta.get("enabled", True) is False:
                continue
            try:
                w = float(meta.get("weight") or 0.0)
            except (TypeError, ValueError):
                continue
            if w > 0:
                weights[str(name)] = w
        if weights:
            cfg["profile_weights"] = weights

    pw = deploy.get("profile_weights")
    if isinstance(pw, dict) and pw and not isinstance(agents, dict):
        # Legacy builder: weights only, no agents block
        cfg["profile_weights"] = {str(k): float(v) for k, v in pw.items() if float(v) > 0}

    exec_cfg = deploy.get("execution")
    if isinstance(exec_cfg, dict) and exec_cfg:
        prev_exec = cfg.get("execution") if isinstance(cfg.get("execution"), dict) else {}
        cfg["execution"] = {**prev_exec, **dict(exec_cfg)}
        if exec_cfg.get("allows_short") is not None:
            cfg["allows_short"] = bool(exec_cfg.get("allows_short"))
        for src_key, dst_key, cast in (
            ("leverage", "leverage", float),
            ("take_profit_pct", "take_profit_pct", float),
            ("stop_loss_pct", "stop_loss_pct", float),
            ("max_position", "max_position", float),
            ("slippage_bps", "slippage_bps", float),
            ("max_hold_bars", "max_hold_bars", int),
        ):
            if exec_cfg.get(src_key) is None:
                continue
            try:
                cfg[dst_key] = cast(exec_cfg[src_key])  # type: ignore[operator]
            except (TypeError, ValueError):
                pass
        use_llm = exec_cfg.get("use_llm_synthesis")
        if use_llm is None:
            use_llm = bool(exec_cfg.get("arbitrator_llm"))
        if use_llm:
            cfg["arbitrator_mode"] = "agent_llm"
            cfg["use_llm"] = True
        elif exec_cfg.get("use_llm_synthesis") is False and exec_cfg.get("arbitrator_llm") is False:
            # Explicit non-LLM deploy from builder — do not inherit agent_llm from file defaults
            cfg["use_llm"] = False
            if not deploy.get("arbitrator_mode"):
                cfg["arbitrator_mode"] = "weighted_convergence"

    dt = deploy.get("decision_threshold")
    if isinstance(dt, dict) and dt:
        cfg["decision_threshold"] = dict(dt)

    am = deploy.get("arbitrator_mode")
    if isinstance(am, str) and am.strip():
        mode = am.strip().lower()
        cfg["arbitrator_mode"] = mode
        if mode in ("agent_llm", "llm", "full_agentic"):
            cfg["use_llm"] = True
        elif mode in ("weighted_convergence", "weighted"):
            cfg["use_llm"] = False

    ap = deploy.get("agent_prompts")
    if isinstance(ap, list) and ap:
        cfg["agent_prompts"] = [dict(x) for x in ap if isinstance(x, dict)]

    profile = deploy.get("profile")
    if isinstance(profile, dict) and profile.get("profile_id"):
        cfg["profile_id"] = str(profile.get("profile_id"))
    elif isinstance(deploy.get("profile_id"), str) and deploy["profile_id"].strip():
        cfg["profile_id"] = deploy["profile_id"].strip()


_MAX_QUICK_SYMBOLS = 8


def _normalize_quick_symbols(ticker: str, symbols: list[str] | None) -> list[str]:
    """Dedupe universe; primary ticker first when present. Cap for API cost."""
    primary = (ticker or "").strip()
    ordered: list[str] = []
    seen: set[str] = set()
    for raw in [primary, *(symbols or [])]:
        s = str(raw or "").strip()
        if not s or s in seen:
            continue
        seen.add(s)
        ordered.append(s)
    if not ordered:
        ordered = ["BTC/USDT"]
    return ordered[:_MAX_QUICK_SYMBOLS]


def _fetch_ohlcv_latest(
    symbol: str,
    *,
    ex_id: str,
    tf: str,
    interval_sec: int,
    fetch_limit: int,
) -> list[list[float]]:
    if ex_id == "futu":
        bars = fetch_futu_ohlcv_bars(symbol, fetch_limit, interval_sec=interval_sec)
    elif ex_id == "yahoo":
        bars = fetch_yfinance_ohlcv_bars(symbol, fetch_limit, interval_sec=interval_sec)
    else:
        bars = fetch_ccxt_ohlcv_bars(symbol, fetch_limit, timeframe=tf, exchange_id=ex_id)
    return [list(map(float, row)) for row in bars]


def _fetch_ohlcv_range(
    symbol: str,
    *,
    ex_id: str,
    tf: str,
    interval_sec: int,
    fetch_since_ms: int,
    until_ms: int,
    max_rows: int,
) -> list[list[float]]:
    if ex_id == "yahoo":
        bars = fetch_yfinance_ohlcv_bars(
            symbol,
            max_rows,
            interval_sec=interval_sec,
            since_ms=fetch_since_ms,
            until_ms=until_ms,
        )
    else:
        bars = fetch_ccxt_ohlcv_range(
            symbol,
            timeframe=tf,
            since_ms=fetch_since_ms,
            until_ms=until_ms,
            exchange_id=ex_id,
            max_rows=max_rows,
        )
    return [list(map(float, row)) for row in bars]


def _execute_quick_backtest(
    req: QuickBacktestRequest,
    *,
    strategy: dict[str, Any] | None = None,
    run_id: str | None = None,
    on_bar_complete: Callable[[int, int, dict[str, Any]], None] | None = None,
    deploy_path: str | None = None,
) -> dict[str, Any]:
    cfg = resolve_backtest_config(deploy_path=deploy_path)
    # Merge inline deploy from aimm-web Strategy Builder / tenant clients.
    # Accepts full deploy.active.json shape (agents + execution), not only
    # legacy profile_weights. Does NOT write deploy.active.json.
    if req.deploy and isinstance(req.deploy, dict):
        _merge_inline_deploy_overrides(cfg, req.deploy)
    set_env_from_config(cfg)
    cap = _max_api_steps()
    tf = interval_sec_to_ccxt_timeframe(int(req.interval_sec))
    ex_id = (req.exchange_id or "binance").strip().lower()
    if ex_id in ("yfinance", "yf"):
        ex_id = "yahoo"

    want = req.max_steps if req.max_steps is not None else req.n_bars
    eval_cap = min(int(want), int(req.n_bars), cap)
    if eval_cap < 1:
        raise HTTPException(status_code=400, detail="No steps to run after applying caps.")

    interval_sec = int(req.interval_sec)
    universe = _normalize_quick_symbols(req.ticker, req.symbols)
    primary = universe[0]
    multi = len(universe) >= 2
    ta_warmup = 0
    eval_steps = eval_cap
    bars: list[list[float]] | None = None
    bars_by_symbol: dict[str, list[list[float]]] | None = None

    if req.since_iso or req.until_iso:
        if not req.since_iso or not req.until_iso:
            raise HTTPException(
                status_code=400, detail="since_iso and until_iso must both be set (or both omitted)"
            )
        if ex_id == "futu":
            raise HTTPException(
                status_code=400,
                detail=(
                    "exchange_id=futu does not support since_iso/until_iso yet; "
                    "omit the date range for latest-N candles, use exchange_id=yahoo, "
                    "or use a CCXT exchange for fixed windows."
                ),
            )
        # Prefetch TA warmup *before* From so the scored window is not shortened.
        eval_since_ms = iso_utc_to_ms(req.since_iso)
        until_ms = _until_ms_inclusive(req.until_iso)
        fetch_since_ms, warmup_plan = warmup_fetch_since_ms(
            eval_since_ms=eval_since_ms,
            interval_sec=interval_sec,
        )
        interval_ms = max(1, interval_sec * 1000)
        approx = int((until_ms - fetch_since_ms) / interval_ms) + 10
        max_rows = min(100_000, max(approx, int(req.n_bars) + warmup_plan))

        if multi:
            raw: dict[str, list[list[float]]] = {}
            for sym in universe:
                series = _fetch_ohlcv_range(
                    sym,
                    ex_id=ex_id,
                    tf=tf,
                    interval_sec=interval_sec,
                    fetch_since_ms=fetch_since_ms,
                    until_ms=until_ms,
                    max_rows=max_rows,
                )
                if len(series) < 2:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Not enough OHLCV bars in range for {sym}.",
                    )
                raw[sym] = series
            aligned = align_bars_by_min_length(raw)
            primary_bars = aligned[primary]
            warmup_idx = split_warmup_index(primary_bars, eval_since_ms=eval_since_ms)
            eval_available = len(primary_bars) - warmup_idx
            if eval_available < 2:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Not enough bars after the From date to evaluate "
                        f"(got {eval_available}; need at least 2). "
                        "Widen the date range or pick an earlier From."
                    ),
                )
            eval_steps = min(eval_available, eval_cap)
            ta_warmup = warmup_idx
            bars_by_symbol = {sym: rows[: warmup_idx + eval_steps] for sym, rows in aligned.items()}
        else:
            bars = _fetch_ohlcv_range(
                primary,
                ex_id=ex_id,
                tf=tf,
                interval_sec=interval_sec,
                fetch_since_ms=fetch_since_ms,
                until_ms=until_ms,
                max_rows=max_rows,
            )
            if len(bars) < 2:
                raise HTTPException(
                    status_code=400, detail="Not enough OHLCV bars in the requested range."
                )
            warmup_idx = split_warmup_index(bars, eval_since_ms=eval_since_ms)
            eval_available = len(bars) - warmup_idx
            if eval_available < 2:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Not enough bars after the From date to evaluate "
                        f"(got {eval_available}; need at least 2). "
                        "Widen the date range or pick an earlier From."
                    ),
                )
            eval_steps = min(eval_available, eval_cap)
            ta_warmup = warmup_idx
            bars = bars[: warmup_idx + eval_steps]
    else:
        # Latest-N: fetch warmup+eval so the first N scored bars stay intact.
        fetch_limit, warmup_plan = total_fetch_bars(eval_steps=eval_cap)
        if multi:
            raw = {}
            for sym in universe:
                series = _fetch_ohlcv_latest(
                    sym,
                    ex_id=ex_id,
                    tf=tf,
                    interval_sec=interval_sec,
                    fetch_limit=fetch_limit,
                )
                if len(series) < 2:
                    raise HTTPException(
                        status_code=400, detail=f"Not enough OHLCV bars returned for {sym}."
                    )
                raw[sym] = series
            aligned = align_bars_by_min_length(raw)
            aligned_len = min((len(v) for v in aligned.values()), default=0)
            ta_warmup = min(warmup_plan, max(0, aligned_len - 2))
            eval_steps = min(eval_cap, max(2, aligned_len - ta_warmup))
            bars_by_symbol = {sym: rows[: ta_warmup + eval_steps] for sym, rows in aligned.items()}
        else:
            bars = _fetch_ohlcv_latest(
                primary,
                ex_id=ex_id,
                tf=tf,
                interval_sec=interval_sec,
                fetch_limit=fetch_limit,
            )
            if len(bars) < 2:
                raise HTTPException(status_code=400, detail="Not enough OHLCV bars returned.")
            ta_warmup = min(warmup_plan, max(0, len(bars) - 2))
            eval_steps = min(eval_cap, max(2, len(bars) - ta_warmup))
            bars = bars[: ta_warmup + eval_steps]

    if run_id is not None and run_id in BACKTEST_JOBS:
        BACKTEST_JOBS[run_id].update(
            {
                "status": "running",
                "total_steps": eval_steps,
                "step": 0,
            }
        )
        _write_job(run_id, dict(BACKTEST_JOBS[run_id]))

    bar_count = min(len(v) for v in bars_by_symbol.values()) if bars_by_symbol else len(bars or [])
    logger.info(
        "backtest quick ticker=%s universe=%s bars=%s eval_steps=%s ta_warmup=%s",
        primary,
        universe,
        bar_count,
        eval_steps,
        ta_warmup,
    )
    result = run_multi_step_backtest(
        ticker=primary,
        bars=None if bars_by_symbol else bars,
        bars_by_symbol=bars_by_symbol,
        initial_cash=req.initial_cash,
        fee_bps=req.fee_bps,
        interval_sec=req.interval_sec,
        runs_dir=RUNS_DIR,
        max_steps=None,
        eval_steps=eval_steps,
        ta_warmup_bars=ta_warmup,
        run_id=run_id,
        progress_callback=on_bar_complete,
        deploy_config=cfg,
        deploy_profile_weights=cfg.get("profile_weights") or None,
        deploy_profile_id=cfg.get("profile_id") or None,
        deploy_arbitrator_mode=cfg.get("arbitrator_mode") or None,
        leverage=float(cfg["leverage"]) if cfg.get("leverage") is not None else None,
        take_profit_pct=float(cfg.get("take_profit_pct") or 0.0),
        stop_loss_pct=float(cfg.get("stop_loss_pct") or 0.0),
        max_hold_bars=int(cfg.get("max_hold_bars") or 0),
    )
    logger.info(
        "backtest done run_id=%s trade_count=%s final_equity=%s",
        result.run_id,
        result.trade_count,
        result.metrics.get("final_equity"),
    )
    out: dict[str, Any] = {
        "run_id": result.run_id,
        "steps": result.steps,
        "trade_count": result.trade_count,
        "metrics": result.metrics,
        "evaluation": _evaluation_block(result=result, initial_cash=req.initial_cash),
        "paths": _backtest_paths_response(result),
        "capped": eval_steps < min(int(want), int(req.n_bars)),
        "server_max_steps": cap,
        "ta_warmup_bars": ta_warmup,
        "eval_bars": eval_steps,
        "symbols": universe,
        "ticker": primary,
    }
    if result.quality_report:
        out["quality_report"] = result.quality_report
    if result.resolved_config:
        out["resolved_config"] = result.resolved_config
    if strategy:
        out["strategy"] = strategy
    return out


def _execute_demo_backtest(
    req: DemoBacktestRequest,
    *,
    run_id: str | None = None,
    on_bar_complete: Callable[[int, int, dict[str, Any]], None] | None = None,
    deploy_path: str | None = None,
    strategy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = resolve_backtest_config(deploy_path=deploy_path)
    set_env_from_config(cfg)
    cap = _max_api_steps()
    want = int(req.steps)
    eval_cap = max(1, min(want, cap))
    fetch_limit, warmup_plan = total_fetch_bars(eval_steps=eval_cap)
    ex_id = (req.exchange_id or "binance").strip().lower()
    if ex_id in ("yfinance", "yf"):
        ex_id = "yahoo"
    tf = interval_sec_to_ccxt_timeframe(int(req.interval_sec))
    syms = [s.strip() for s in (req.symbols or "").split(",") if s.strip()]
    if len(syms) < 2:
        raise HTTPException(
            status_code=400, detail="demo backtest requires at least 2 symbols (comma-separated)"
        )

    if run_id is not None and run_id in BACKTEST_JOBS:
        BACKTEST_JOBS[run_id].update({"status": "running", "total_steps": eval_cap, "step": 0})
        _write_job(run_id, dict(BACKTEST_JOBS[run_id]))

    bars_by_symbol: dict[str, list[list[float]]] = {}
    for sym in syms:
        if ex_id == "futu":
            bars = fetch_futu_ohlcv_bars(
                sym,
                fetch_limit,
                interval_sec=int(req.interval_sec),
            )
        elif ex_id == "yahoo":
            bars = fetch_yfinance_ohlcv_bars(
                sym,
                fetch_limit,
                interval_sec=int(req.interval_sec),
            )
        else:
            bars = fetch_ccxt_ohlcv_bars(
                exchange_id=ex_id, symbol=sym, timeframe=tf, limit=fetch_limit
            )
        if not bars:
            raise HTTPException(
                status_code=400, detail=f"No OHLCV returned for {sym} ({ex_id}, {tf})"
            )
        bars_by_symbol[sym] = [list(map(float, row)) for row in bars]

    aligned = align_bars_by_min_length(bars_by_symbol)
    aligned_len = min((len(v) for v in aligned.values()), default=0)
    ta_warmup = min(warmup_plan, max(0, aligned_len - 2))
    eval_steps = min(eval_cap, max(2, aligned_len - ta_warmup))
    aligned = {sym: rows[: ta_warmup + eval_steps] for sym, rows in aligned.items()}

    # Use the first symbol as "primary" for logging/bench labels inside the engine.
    primary = syms[0]
    result = run_multi_step_backtest(
        ticker=primary,
        bars_by_symbol=aligned,
        initial_cash=req.initial_cash,
        fee_bps=req.fee_bps,
        interval_sec=req.interval_sec,
        runs_dir=RUNS_DIR,
        max_steps=None,
        eval_steps=eval_steps,
        ta_warmup_bars=ta_warmup,
        run_id=run_id,
        progress_callback=on_bar_complete,
        deploy_config=cfg,
        deploy_profile_weights=cfg.get("profile_weights") or None,
        deploy_profile_id=cfg.get("profile_id") or None,
        deploy_arbitrator_mode=cfg.get("arbitrator_mode") or None,
        take_profit_pct=cfg.get("take_profit_pct", 0.0),
        stop_loss_pct=cfg.get("stop_loss_pct", 0.0),
        max_hold_bars=cfg.get("max_hold_bars", 0),
    )

    out: dict[str, Any] = {
        "run_id": result.run_id,
        "steps": result.steps,
        "trade_count": result.trade_count,
        "metrics": result.metrics,
        "evaluation": _evaluation_block(result=result, initial_cash=req.initial_cash),
        "paths": _backtest_paths_response(result),
        "capped": eval_steps < want,
        "server_max_steps": cap,
        "symbols": syms,
        "timeframe": tf,
        "exchange_id": ex_id,
        "ta_warmup_bars": ta_warmup,
        "eval_bars": eval_steps,
    }
    if result.quality_report:
        out["quality_report"] = result.quality_report
    if result.resolved_config:
        out["resolved_config"] = result.resolved_config
    if strategy:
        out["strategy"] = strategy
    return out


def _deploy_is_agentic(deploy: dict[str, Any]) -> bool:
    exec_cfg = deploy.get("execution") if isinstance(deploy.get("execution"), dict) else {}
    if exec_cfg.get("use_llm_synthesis") or exec_cfg.get("arbitrator_llm"):
        return True
    agents = deploy.get("agents") if isinstance(deploy.get("agents"), dict) else {}
    return any(
        isinstance(meta, dict)
        and meta.get("llm_enabled")
        and meta.get("enabled", True) is not False
        for meta in agents.values()
    )


def _embedded_worker_enabled() -> bool:
    v = (os.getenv("AIMM_BACKTEST_WORKER_EMBEDDED") or "1").strip().lower()
    return v not in {"0", "false", "no", "off"}


_AGENTIC_SEM = threading.Semaphore(max(1, int(os.getenv("AIMM_BACKTEST_MAX_AGENTIC", "2"))))
_DET_SEM = threading.Semaphore(max(1, int(os.getenv("AIMM_BACKTEST_MAX_DETERMINISTIC", "8"))))


def execute_queued_backtest_job(run_id: str) -> None:
    """Run a previously enqueued /backtests/run job (API thread or external worker)."""
    rid = require_safe_id(str(run_id), name="run_id")
    job = {}
    p = _job_path(rid)
    if p.is_file():
        try:
            loaded = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                job = loaded
        except Exception:
            job = {}
    payload = job.get("request") if isinstance(job.get("request"), dict) else {}
    deploy_path = str(job.get("deploy_path") or "")
    if str(job.get("status") or "") not in ("queued", "claimed"):
        return
    if not deploy_path or not Path(deploy_path).is_file():
        _fail_job(rid, job, error="Missing per-run deploy.json")
        return
    job["status"] = "claimed"
    _write_job(rid, job)

    agentic = bool(job.get("agentic"))
    sem = _AGENTIC_SEM if agentic else _DET_SEM

    def on_bar(i: int, total: int, snap: dict[str, Any]) -> None:
        _job_progress_update(rid, i, total, snap)

    sem.acquire()
    try:
        from config.deploy_context import thread_deploy_path
        from llm.usage import reset_usage, snapshot_usage

        BACKTEST_JOBS[rid] = {**job, "status": "running"}
        _write_job(rid, dict(BACKTEST_JOBS[rid]))
        reset_usage()
        symbols = str(payload.get("symbols") or "")
        syms = [s.strip() for s in symbols.split(",") if s.strip()]
        with thread_deploy_path(deploy_path):
            if len(syms) >= 2:
                demo = DemoBacktestRequest(
                    symbols=",".join(syms),
                    steps=int(payload.get("n_bars") or 180),
                    interval_sec=int(payload.get("interval_sec") or 3600),
                    exchange_id=str(payload.get("exchange_id") or "binance"),
                    initial_cash=float(payload.get("initial_cash") or 100_000),
                    fee_bps=float(payload.get("fee_bps") or 5),
                )
                out = _execute_demo_backtest(
                    demo, run_id=rid, on_bar_complete=on_bar, deploy_path=deploy_path
                )
            else:
                q = QuickBacktestRequest(
                    ticker=str(payload.get("ticker") or "BTC/USDT"),
                    n_bars=int(payload.get("n_bars") or 180),
                    interval_sec=int(payload.get("interval_sec") or 3600),
                    initial_cash=float(payload.get("initial_cash") or 100_000),
                    fee_bps=float(payload.get("fee_bps") or 5),
                    exchange_id=str(payload.get("exchange_id") or "binance"),
                    since_iso=payload.get("since_iso"),
                    until_iso=payload.get("until_iso"),
                )
                out = _execute_quick_backtest(
                    q, run_id=rid, on_bar_complete=on_bar, deploy_path=deploy_path
                )
        out["usage"] = snapshot_usage()
        BACKTEST_JOBS[rid] = {"status": "completed", "result": out, "usage": out["usage"]}
        _write_job(rid, dict(BACKTEST_JOBS[rid]))
    except HTTPException as e:
        detail = e.detail
        _fail_job(rid, job, error=detail if isinstance(detail, str) else str(detail))
    except Exception as e:
        logger.exception("queued backtest failed run_id=%s", rid)
        _fail_job(rid, job, error=str(e))
    finally:
        sem.release()


def iter_queued_backtest_ids(*, limit: int = 8) -> list[str]:
    if not BACKTESTS_DIR.is_dir():
        return []
    out: list[str] = []
    for d in sorted(BACKTESTS_DIR.iterdir(), key=lambda p: p.stat().st_mtime if p.exists() else 0):
        if not d.is_dir():
            continue
        p = d / "job.json"
        if not p.is_file():
            continue
        try:
            job = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if (
            isinstance(job, dict)
            and str(job.get("status") or "") == "queued"
            and job.get("deploy_path")
        ):
            out.append(d.name)
        if len(out) >= limit:
            break
    return out


@router.post("/backtests/run")
def post_strategy_backtest_run(req: StrategyBacktestRequest) -> dict[str, Any]:
    """Enqueue a tenant-isolated backtest with inline deploy JSON."""
    if not isinstance(req.deploy, dict) or not req.deploy.get("agents"):
        raise HTTPException(status_code=400, detail="deploy.agents is required")

    from api.tenant_deploy import write_tenant_deploy

    rid = f"bt-{uuid.uuid4().hex[:12]}"
    deploy_path = write_tenant_deploy(deploy=req.deploy, run_id=rid, user_id=req.user_id or "anon")
    agentic = _deploy_is_agentic(req.deploy)
    BACKTEST_JOBS[rid] = {
        "status": "queued",
        "step": 0,
        "total_steps": 0,
        "trade_count": 0,
        "equity": None,
        "user_id": req.user_id,
        "strategy_id": req.strategy_id,
        "deploy_path": str(deploy_path),
        "agentic": agentic,
        "request": req.model_dump(),
    }
    _write_job(rid, dict(BACKTEST_JOBS[rid]))
    if _embedded_worker_enabled():
        threading.Thread(target=execute_queued_backtest_job, args=(rid,), daemon=True).start()
    return {"run_id": rid, "poll": f"/backtests/jobs/{rid}", "agentic": agentic}


@router.post("/backtests/quick")
def post_quick_backtest(req: QuickBacktestRequest) -> dict[str, Any]:
    """
    Run a **synthetic** OHLCV replay: one LangGraph pass per bar, book simulated fills,
    write ``.runs/backtests/<run_id>/`` (trades, equity, ``iterations.jsonl``, summary) and flow events under ``.runs/``.
    """
    return _execute_quick_backtest(req)


@router.post("/backtests/quick/async")
def post_quick_backtest_async(req: QuickBacktestRequest) -> dict[str, Any]:
    """Run quick backtest in a background thread and expose per-bar progress.

    Poll :func:`get_backtest_job` for progress updates.
    """
    rid = f"bt-{uuid.uuid4().hex[:12]}"
    BACKTEST_JOBS[rid] = {
        "status": "queued",
        "step": 0,
        "total_steps": 0,
        "trade_count": 0,
        "equity": None,
        "capital": None,
        "positions": 0,
        "ts": None,
    }
    _write_job(rid, dict(BACKTEST_JOBS[rid]))

    def work() -> None:
        def on_bar(i: int, total: int, snap: dict[str, Any]) -> None:
            _job_progress_update(rid, i, total, snap)

        try:
            out = _execute_quick_backtest(req, run_id=rid, on_bar_complete=on_bar)
            BACKTEST_JOBS[rid] = {"status": "completed", "result": out}
            _write_job(rid, dict(BACKTEST_JOBS[rid]))
        except HTTPException as e:
            detail = e.detail
            BACKTEST_JOBS[rid] = {
                "status": "failed",
                "error": detail if isinstance(detail, str) else str(detail),
            }
            _write_job(rid, dict(BACKTEST_JOBS[rid]))
        except Exception as e:
            logger.exception("async quick backtest failed")
            BACKTEST_JOBS[rid] = {"status": "failed", "error": str(e)}
            _write_job(rid, dict(BACKTEST_JOBS[rid]))

    threading.Thread(target=work, daemon=True).start()
    return {"run_id": rid, "poll": f"/backtests/jobs/{rid}"}


@router.post("/backtests/demo/async")
def post_demo_backtest_async(req: DemoBacktestRequest) -> dict[str, Any]:
    """Run README-style multi-symbol demo backtest (async) with job polling."""
    rid = f"bt-{uuid.uuid4().hex[:12]}"
    BACKTEST_JOBS[rid] = {
        "status": "queued",
        "step": 0,
        "total_steps": 0,
        "trade_count": 0,
        "equity": None,
        "capital": None,
        "positions": 0,
        "ts": None,
    }
    _write_job(rid, dict(BACKTEST_JOBS[rid]))

    def work() -> None:
        def on_bar(i: int, total: int, snap: dict[str, Any]) -> None:
            _job_progress_update(rid, i, total, snap)

        try:
            out = _execute_demo_backtest(req, run_id=rid, on_bar_complete=on_bar)
            BACKTEST_JOBS[rid] = {"status": "completed", "result": out}
            _write_job(rid, dict(BACKTEST_JOBS[rid]))
        except HTTPException as e:
            detail = e.detail
            BACKTEST_JOBS[rid] = {
                "status": "failed",
                "error": detail if isinstance(detail, str) else str(detail),
            }
            _write_job(rid, dict(BACKTEST_JOBS[rid]))
        except Exception as e:
            logger.exception("async demo backtest failed")
            BACKTEST_JOBS[rid] = {"status": "failed", "error": str(e)}
            _write_job(rid, dict(BACKTEST_JOBS[rid]))

    threading.Thread(target=work, daemon=True).start()
    return {"run_id": rid, "poll": f"/backtests/jobs/{rid}"}


class PresetBacktestRequest(BaseModel):
    preset_id: str = Field(DEFAULT_QUANT_STRATEGY_ID, min_length=1)
    ticker: str = Field("BTC/USDT", min_length=3)
    exchange_id: str | None = Field(
        None,
        description='Optional: "binance" (default), "yahoo", or "futu".',
    )
    n_bars: int | None = Field(None, ge=20, le=100_000)
    interval_sec: int | None = Field(None, ge=60, le=86_400)
    max_steps: int | None = Field(None, ge=1)
    seed: int | None = Field(None, ge=0)
    fee_bps: float | None = Field(None, ge=0, le=500)
    initial_cash: float | None = Field(None, gt=0)
    since_iso: str | None = Field(None, description="Optional UTC range start (with until_iso).")
    until_iso: str | None = Field(None, description="Optional UTC range end (with since_iso).")


@router.get("/strategies")
def get_strategy_presets() -> dict[str, Any]:
    """List named strategy presets and default parameters for the backtest UI."""
    return {"strategies": list_presets()}


@router.post("/backtests/preset")
def post_preset_backtest(req: PresetBacktestRequest) -> dict[str, Any]:
    """Run a quick backtest using a **named preset** (defaults for bars, interval, caps)."""
    try:
        merged = merge_preset_quick_request(
            req.preset_id,
            ticker=req.ticker,
            exchange_id=req.exchange_id,
            n_bars=req.n_bars,
            interval_sec=req.interval_sec,
            max_steps=req.max_steps,
            seed=req.seed,
            fee_bps=req.fee_bps,
            initial_cash=req.initial_cash,
            since_iso=req.since_iso,
            until_iso=req.until_iso,
        )
        preset = get_preset(req.preset_id)
    except KeyError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    deploy_path = str(merged.pop("deploy_path", "") or "") or None
    symbols = str(merged.pop("symbols", "") or "").strip()
    merged.pop("preset_id", None)
    merged.pop("seed", None)  # not on QuickBacktestRequest

    strategy_meta = {
        "preset_id": preset.id,
        "title": preset.title,
        "description": preset.description,
        "deploy_path": deploy_path,
        "arbitrator_mode": "agent_llm",
    }

    # Multi-symbol weighted presets use the demo basket path (matches profitable agentic runs).
    sym_list = [s.strip() for s in symbols.split(",") if s.strip()]
    if len(sym_list) >= 2 and not (req.since_iso and req.until_iso):
        demo = DemoBacktestRequest(
            symbols=symbols,
            steps=int(merged.get("max_steps") or merged.get("n_bars") or 200),
            interval_sec=int(merged.get("interval_sec") or 86_400),
            exchange_id=str(merged.get("exchange_id") or "binance"),
            initial_cash=float(merged.get("initial_cash") or 100_000),
            fee_bps=float(merged.get("fee_bps") or 10),
        )
        return _execute_demo_backtest(demo, deploy_path=deploy_path, strategy=strategy_meta)

    q = QuickBacktestRequest(
        **{k: v for k, v in merged.items() if k in QuickBacktestRequest.model_fields}
    )
    return _execute_quick_backtest(
        q,
        strategy=strategy_meta,
        deploy_path=deploy_path,
    )


@router.post("/backtests/preset/async")
def post_preset_backtest_async(req: PresetBacktestRequest) -> dict[str, Any]:
    """Run preset backtest in a background thread.

    Poll :func:`get_backtest_job` for per-bar progress.
    """
    try:
        merged = merge_preset_quick_request(
            req.preset_id,
            ticker=req.ticker,
            exchange_id=req.exchange_id,
            n_bars=req.n_bars,
            interval_sec=req.interval_sec,
            max_steps=req.max_steps,
            seed=req.seed,
            fee_bps=req.fee_bps,
            initial_cash=req.initial_cash,
            since_iso=req.since_iso,
            until_iso=req.until_iso,
        )
        preset = get_preset(req.preset_id)
    except KeyError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    deploy_path = str(merged.pop("deploy_path", "") or "") or None
    symbols = str(merged.pop("symbols", "") or "").strip()
    merged.pop("preset_id", None)
    merged.pop("seed", None)

    strategy_meta = {
        "preset_id": preset.id,
        "title": preset.title,
        "description": preset.description,
        "deploy_path": deploy_path,
        "arbitrator_mode": "agent_llm",
    }
    sym_list = [s.strip() for s in symbols.split(",") if s.strip()]
    use_demo = len(sym_list) >= 2 and not (req.since_iso and req.until_iso)

    rid = f"bt-{uuid.uuid4().hex[:12]}"
    BACKTEST_JOBS[rid] = {
        "status": "queued",
        "step": 0,
        "total_steps": 0,
        "trade_count": 0,
        "equity": None,
        "vetoed": None,
    }
    _write_job(rid, dict(BACKTEST_JOBS[rid]))

    def work() -> None:
        def on_bar(i: int, total: int, snap: dict[str, Any]) -> None:
            _job_progress_update(rid, i, total, snap)

        try:
            if use_demo:
                demo = DemoBacktestRequest(
                    symbols=symbols,
                    steps=int(merged.get("max_steps") or merged.get("n_bars") or 200),
                    interval_sec=int(merged.get("interval_sec") or 86_400),
                    exchange_id=str(merged.get("exchange_id") or "binance"),
                    initial_cash=float(merged.get("initial_cash") or 100_000),
                    fee_bps=float(merged.get("fee_bps") or 10),
                )
                out = _execute_demo_backtest(
                    demo,
                    run_id=rid,
                    on_bar_complete=on_bar,
                    deploy_path=deploy_path,
                    strategy=strategy_meta,
                )
            else:
                q = QuickBacktestRequest(
                    **{k: v for k, v in merged.items() if k in QuickBacktestRequest.model_fields}
                )
                out = _execute_quick_backtest(
                    q,
                    strategy=strategy_meta,
                    run_id=rid,
                    on_bar_complete=on_bar,
                    deploy_path=deploy_path,
                )
            BACKTEST_JOBS[rid] = {"status": "completed", "result": out}
            _write_job(rid, dict(BACKTEST_JOBS[rid]))
        except HTTPException as e:
            detail = e.detail
            BACKTEST_JOBS[rid] = {
                "status": "failed",
                "error": detail if isinstance(detail, str) else str(detail),
            }
            _write_job(rid, dict(BACKTEST_JOBS[rid]))
        except Exception as e:
            logger.exception("async preset backtest failed")
            BACKTEST_JOBS[rid] = {"status": "failed", "error": str(e)}
            _write_job(rid, dict(BACKTEST_JOBS[rid]))

    threading.Thread(target=work, daemon=True).start()
    return {"run_id": rid, "poll": f"/backtests/jobs/{rid}"}


@router.get("/backtests/jobs/{run_id}")
def get_backtest_job(run_id: str) -> dict[str, Any]:
    rid = str(run_id)
    job: dict[str, Any] | None = None
    # Prefer live in-memory state when this worker owns the thread.
    mem = BACKTEST_JOBS.get(rid)
    if isinstance(mem, dict) and mem.get("status") in ("running", "queued", "completed", "failed"):
        job = dict(mem)
    p = _job_path(rid)
    if job is None and p.is_file():
        try:
            loaded = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                job = loaded
        except Exception:
            job = None
    if job is None:
        raise HTTPException(status_code=404, detail="Unknown job or run_id")
    return _maybe_fail_stale_job(rid, job)


@router.post("/backtests/jobs/{run_id}/cancel")
def cancel_backtest_job(run_id: str) -> dict[str, bool]:
    """Cancel a running/queued backtest job so polls see it as failed immediately."""
    rid = str(run_id).strip()
    if not rid:
        raise HTTPException(status_code=400, detail="run_id is required")
    p = _job_path(rid)
    if not p.is_file():
        raise HTTPException(status_code=404, detail="Unknown job or run_id")
    try:
        job = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid job file") from None
    status = str(job.get("status") or "")
    if status not in ("running", "queued"):
        raise HTTPException(status_code=409, detail=f"Job is {status}, not running/queued")
    _fail_job(rid, job, error="Cancelled by user")
    # Also remove from in-memory tracking so the thread stops if it checks
    if rid in BACKTEST_JOBS:
        BACKTEST_JOBS[rid] = {"status": "cancelled", "error": "Cancelled by user"}
    logger.info("cancelled backtest run_id=%s", rid)
    return {"ok": True}


@router.get("/backtests/jobs/{run_id}/stream")
async def stream_backtest_job(run_id: str, request: Request) -> StreamingResponse:
    """Server-sent events stream of backtest job progress.

    This is a drop-in upgrade over polling for large fanout. Clients should close the stream
    once they receive a terminal state (completed/failed).
    """

    rid = str(run_id)

    async def gen():
        last_payload: str | None = None
        last_keepalive = 0.0
        while True:
            if await request.is_disconnected():
                return

            try:
                job = get_backtest_job(rid)
            except HTTPException as e:
                # One structured error event, then end.
                payload = json.dumps(
                    {"status": "failed", "error": str(e.detail)}, ensure_ascii=False
                )
                yield f"event: error\ndata: {payload}\n\n"
                return

            payload = json.dumps(job, ensure_ascii=False)
            if payload != last_payload:
                last_payload = payload
                yield f"data: {payload}\n\n"

            status = (job or {}).get("status")
            if status in ("completed", "failed"):
                return

            # Keep-alive comment ~ every 15s to prevent idle timeouts.
            now = anyio.current_time()
            if now - last_keepalive > 15.0:
                last_keepalive = now
                yield ": keepalive\n\n"

            await anyio.sleep(0.5)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


class FileBacktestRequest(BaseModel):
    path: str = Field(..., description="Path under repo to JSON OHLCV file (server-local).")
    initial_cash: float = Field(100_000.0, gt=0)
    fee_bps: float = Field(10.0, ge=0, le=500)
    interval_sec: int = Field(300, ge=60, le=86_400)
    max_steps: int | None = Field(None, ge=1)


@router.post("/backtests/from-file")
def post_backtest_from_file(req: FileBacktestRequest) -> dict[str, Any]:
    """Load bars from a server-local JSON file (operator path; not multipart upload)."""
    p = Path(req.path)
    if not p.is_file():
        raise HTTPException(status_code=404, detail=f"File not found: {req.path}")
    try:
        ticker, bars = load_ohlcv_json(p)
    except (OSError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    cap = _max_api_steps()
    want = req.max_steps if req.max_steps is not None else len(bars)
    effective = min(want, len(bars), cap)
    cfg = resolve_backtest_config()
    set_env_from_config(cfg)
    result = run_multi_step_backtest(
        ticker=ticker,
        bars=bars,
        initial_cash=req.initial_cash,
        fee_bps=req.fee_bps,
        interval_sec=req.interval_sec,
        runs_dir=RUNS_DIR,
        max_steps=effective,
        deploy_config=cfg,
        deploy_profile_weights=cfg.get("profile_weights") or None,
        deploy_profile_id=cfg.get("profile_id") or None,
        deploy_arbitrator_mode=cfg.get("arbitrator_mode") or None,
        take_profit_pct=cfg.get("take_profit_pct", 0.0),
        stop_loss_pct=cfg.get("stop_loss_pct", 0.0),
        max_hold_bars=cfg.get("max_hold_bars", 0),
    )
    out: dict[str, Any] = {
        "run_id": result.run_id,
        "steps": result.steps,
        "trade_count": result.trade_count,
        "metrics": result.metrics,
        "evaluation": _evaluation_block(result=result, initial_cash=req.initial_cash),
        "paths": _backtest_paths_response(result),
        "capped": effective < min(want, len(bars)),
        "server_max_steps": cap,
    }
    if result.quality_report:
        out["quality_report"] = result.quality_report
    if result.resolved_config:
        out["resolved_config"] = result.resolved_config
    return out


@router.get("/backtests/{run_id}/summary")
def get_backtest_summary(run_id: str) -> dict[str, Any]:
    summary_path = _run_dir(run_id) / "summary.json"
    if not summary_path.is_file():
        raise HTTPException(status_code=404, detail="Unknown backtest run_id")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    # Backfill start/end time for older runs (added later in engine).
    if isinstance(summary, dict) and ("start_ts" not in summary or "end_ts" not in summary):
        equity_path = _run_dir(run_id) / "equity.jsonl"
        if equity_path.is_file():
            try:
                lines = equity_path.read_text(encoding="utf-8").splitlines()
                if lines:
                    first = json.loads(lines[0])
                    last = json.loads(lines[-1])
                    start_ts = first.get("ts")
                    end_ts = last.get("ts")
                    if isinstance(start_ts, (int, float)) and isinstance(end_ts, (int, float)):
                        start_ts_i = int(start_ts)
                        end_ts_i = int(end_ts)
                        summary["start_ts"] = start_ts_i
                        summary["end_ts"] = end_ts_i
                        try:
                            from datetime import datetime, timezone

                            summary["start_iso"] = datetime.fromtimestamp(
                                start_ts_i / 1000, tz=timezone.utc
                            ).isoformat()
                            summary["end_iso"] = datetime.fromtimestamp(
                                end_ts_i / 1000, tz=timezone.utc
                            ).isoformat()
                        except Exception:
                            summary.setdefault("start_iso", None)
                            summary.setdefault("end_iso", None)
            except Exception:
                pass
    return summary


@router.get("/backtests/{run_id}/export/manifest")
def get_backtest_export_manifest(run_id: str) -> dict[str, Any]:
    """Return the ``export_manifest.json`` for a completed backtest.

    Contains schema version, file listing, and metrics summary.
    Returns 404 if the export bundle was not generated.
    """
    manifest_path = _run_dir(run_id) / "export_manifest.json"
    if not manifest_path.is_file():
        raise HTTPException(
            status_code=404,
            detail=(
                "export_manifest.json not found — run may be too old "
                "or did not complete export bundle generation"
            ),
        )
    return json.loads(manifest_path.read_text(encoding="utf-8"))


@router.get("/backtests/{run_id}/equity")
def get_backtest_equity(
    run_id: str,
    max_points: int = Query(2000, ge=10, le=50_000),
) -> dict[str, Any]:
    """Return equity curve points for charting (downsampled for large runs)."""
    equity_path = _run_dir(run_id) / "equity.jsonl"
    if not equity_path.is_file():
        raise HTTPException(
            status_code=404, detail="Unknown backtest run_id or missing equity.jsonl"
        )
    rows = _read_jsonl_all(equity_path)
    # Older runs wrote warmup bars into equity.jsonl; drop them so charts match
    # the scored From→To window (same as buy&hold / bars.json).
    summary_path = _run_dir(run_id) / "summary.json"
    if summary_path.is_file():
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            warmup = int(summary.get("ta_warmup_bars") or 0) if isinstance(summary, dict) else 0
            total_bars = int(summary.get("total_bars") or 0) if isinstance(summary, dict) else 0
            # Only trim when equity still contains the full (warmup+eval) series.
            if warmup > 0 and total_bars > warmup and len(rows) >= total_bars:
                rows = rows[warmup:]
        except Exception:
            pass
    raw_count = len(rows)
    sampled = _downsample_rows(rows, max_points) if raw_count > max_points else rows
    # Preserve original indices when downsampling so step stays meaningful.
    if raw_count > len(sampled) and sampled:
        # Rebuild with correct step from full list positions.
        full_norm = [
            _normalize_equity_point(r, i) for i, r in enumerate(rows) if isinstance(r, dict)
        ]
        points = _downsample_rows(full_norm, max_points)
    else:
        points = [
            _normalize_equity_point(r, i) for i, r in enumerate(sampled) if isinstance(r, dict)
        ]
    return {
        "run_id": run_id,
        "count": raw_count,
        "max_points": max_points,
        "downsampled": raw_count > len(points),
        "points": points,
    }


@router.get("/backtests/{run_id}/trades")
def get_backtest_trades(
    run_id: str,
    limit: int = Query(2000, ge=1, le=50_000),
) -> dict[str, Any]:
    """Return booked trades from ``trades.jsonl`` (newest last; capped by ``limit``)."""
    trades_path = _run_dir(run_id) / "trades.jsonl"
    if not trades_path.is_file():
        raise HTTPException(
            status_code=404, detail="Unknown backtest run_id or missing trades.jsonl"
        )
    rows = _read_jsonl_all(trades_path)
    total = len(rows)
    if total > limit:
        rows = rows[-limit:]
    normalized = [normalize_trade_row_for_api(r) for r in rows]
    return {
        "run_id": run_id,
        "total": total,
        "returned": len(normalized),
        "truncated": total > len(normalized),
        "trades": normalized,
    }


@router.get("/backtests/{run_id}/iterations")
def get_backtest_iterations(
    run_id: str,
    limit: int = Query(300, ge=1, le=5000),
) -> dict[str, Any]:
    """Return per-bar iteration receipts from ``iterations.jsonl`` (capped)."""

    iterations_path = _run_dir(run_id) / "iterations.jsonl"
    if not iterations_path.is_file():
        raise HTTPException(
            status_code=404, detail="Unknown backtest run_id or missing iterations.jsonl"
        )
    rows = _jsonl_preview(iterations_path, limit=limit)
    return {
        "run_id": run_id,
        "total_returned": len(rows),
        "iterations": rows,
    }


@router.get("/backtests/{run_id}/attribution")
def get_backtest_attribution(run_id: str) -> dict[str, Any]:
    """Return P&L attribution summary — desk-level, symbol-level, Sharpe per desk."""
    att_summary = _run_dir(run_id) / "attribution_summary.json"
    if not att_summary.is_file():
        raise HTTPException(
            status_code=404,
            detail="attribution_summary.json not found — ensure agentic_batch mode is enabled",
        )
    return json.loads(att_summary.read_text(encoding="utf-8"))


@router.get("/backtests/{run_id}/bars")
def get_backtest_bars(
    run_id: str,
    max_points: int = Query(2000, ge=10, le=50_000),
) -> dict[str, Any]:
    """Return OHLCV bars used for the run (primary ticker), downsampled for charting."""
    bars_path = _run_dir(run_id) / "bars.json"
    if not bars_path.is_file():
        raise HTTPException(status_code=404, detail="Unknown backtest run_id or missing bars.json")
    raw = json.loads(bars_path.read_text(encoding="utf-8"))
    bars = raw.get("bars")
    if not isinstance(bars, list):
        raise HTTPException(status_code=500, detail="bars.json is invalid (missing bars)")
    raw_count = len(bars)
    dict_bars = [b for b in bars if isinstance(b, dict)]
    full_norm = [_normalize_ohlcv_bar(b, i) for i, b in enumerate(dict_bars)]
    points = _downsample_rows(full_norm, max_points) if raw_count > max_points else full_norm
    bench_eq = raw.get("benchmark_equity")
    if not isinstance(bench_eq, list) or not bench_eq:
        bench_eq = raw.get("benchmark_equity_points")
    benchmark_equity: list[float] = []
    if isinstance(bench_eq, list):
        for v in bench_eq:
            try:
                if isinstance(v, dict):
                    benchmark_equity.append(float(v["equity"]))
                else:
                    benchmark_equity.append(float(v))
            except (TypeError, ValueError, KeyError):
                continue

    # Synthesize buy&hold from closes when summary has no benchmark path
    if not benchmark_equity and full_norm:
        summary_path = _run_dir(run_id) / "summary.json"
        initial = 10_000.0
        if summary_path.is_file():
            try:
                summary = json.loads(summary_path.read_text(encoding="utf-8"))
                if isinstance(summary, dict) and summary.get("initial_cash") is not None:
                    initial = float(summary["initial_cash"])
            except Exception:
                pass
        closes: list[float] = []
        for b in full_norm:
            try:
                closes.append(float(b.get("close")))
            except (TypeError, ValueError):
                closes.append(float("nan"))
        if closes and closes[0] and closes[0] == closes[0]:  # not NaN
            c0 = closes[0]
            benchmark_equity = [
                round(initial * (c / c0), 8) if c == c and c0 else initial for c in closes
            ]

    # Align benchmark length to returned (possibly downsampled) bar steps by index map.
    if benchmark_equity and len(benchmark_equity) == raw_count and len(points) != raw_count:
        idxs = [int(p.get("step", i)) for i, p in enumerate(points)]
        benchmark_equity = [
            benchmark_equity[i] if 0 <= i < len(benchmark_equity) else benchmark_equity[-1]
            for i in idxs
        ]
    elif benchmark_equity and len(benchmark_equity) != len(points):
        # Trim/pad benchmark to chart length
        if len(benchmark_equity) > len(points):
            benchmark_equity = benchmark_equity[: len(points)]
        else:
            last = benchmark_equity[-1]
            benchmark_equity = benchmark_equity + [last] * (len(points) - len(benchmark_equity))

    return {
        "run_id": run_id,
        "ticker": raw.get("ticker"),
        "benchmark_symbol": raw.get("benchmark_symbol") or raw.get("ticker"),
        "interval_sec": raw.get("interval_sec"),
        "fill_model": raw.get("fill_model"),
        "count": raw_count,
        "max_points": max_points,
        "downsampled": raw_count > len(points),
        "bars": points,
        "benchmark_equity": benchmark_equity,
    }


def _backtest_list_item(run_dir: Path) -> dict[str, Any] | None:
    """Compact metadata for the Saved-run picker (skips dirs without summary.json)."""
    summary_path = run_dir / "summary.json"
    if not summary_path.is_file():
        return None
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(summary, dict):
        return None
    metrics = summary.get("metrics") if isinstance(summary.get("metrics"), dict) else {}
    symbols = summary.get("symbols")
    if isinstance(symbols, list) and symbols:
        ticker = ",".join(str(s) for s in symbols[:3])
    else:
        ticker = str(summary.get("ticker") or summary.get("benchmark_symbol") or "")
    bars_n = 0
    bars_path = run_dir / "bars.json"
    if bars_path.is_file():
        try:
            raw_bars = json.loads(bars_path.read_text(encoding="utf-8"))
            if isinstance(raw_bars, list):
                bars_n = len(raw_bars)
            elif isinstance(raw_bars, dict) and isinstance(raw_bars.get("bars"), list):
                bars_n = len(raw_bars["bars"])
        except Exception:
            bars_n = int(summary.get("eval_bars") or summary.get("total_bars") or 0)
    else:
        bars_n = int(summary.get("eval_bars") or summary.get("total_bars") or 0)
    equity_n = 0
    eq_path = run_dir / "equity.jsonl"
    if eq_path.is_file():
        try:
            equity_n = sum(
                1 for line in eq_path.read_text(encoding="utf-8").splitlines() if line.strip()
            )
        except Exception:
            equity_n = 0
    end_ts = summary.get("end_ts")
    try:
        if end_ts is not None:
            sort_ts = float(end_ts)
            # Normalize seconds → ms so sorting matches candle timestamps.
            if sort_ts > 0 and sort_ts < 1e12:
                sort_ts *= 1000.0
        else:
            sort_ts = float(run_dir.stat().st_mtime) * 1000.0
    except (TypeError, ValueError, OSError):
        sort_ts = 0.0
    ret = metrics.get("total_return_pct")
    if ret is None:
        init_c = summary.get("initial_cash")
        fin_e = summary.get("final_equity")
        try:
            if init_c and fin_e is not None and float(init_c) > 0:
                ret = (float(fin_e) - float(init_c)) / float(init_c) * 100.0
        except (TypeError, ValueError):
            ret = None
    pnl = metrics.get("total_pnl_usd")
    if pnl is None:
        try:
            init_c = float(summary.get("initial_cash") or 0)
            fin_e = float(summary.get("final_equity") or 0)
            if init_c or fin_e:
                pnl = fin_e - init_c
        except (TypeError, ValueError):
            pnl = None
    return {
        "run_id": run_dir.name,
        "ticker": ticker or None,
        "start_iso": summary.get("start_iso"),
        "end_iso": summary.get("end_iso"),
        "interval_sec": summary.get("interval_sec") or summary.get("bar_interval_sec_inferred"),
        "initial_cash": summary.get("initial_cash"),
        "final_equity": summary.get("final_equity"),
        "total_return_pct": ret,
        "total_pnl_usd": pnl,
        "sharpe": metrics.get("sharpe"),
        "total_trades": metrics.get("total_trades") or summary.get("trade_count"),
        "eval_bars": summary.get("eval_bars") or bars_n,
        "equity_points": equity_n,
        "has_charts": bars_n >= 8 and equity_n >= 8,
        "sort_ts": sort_ts,
    }


@router.get("/backtests")
def list_backtests() -> dict[str, Any]:
    """List completed backtests with picker metadata (newest first).

    ``runs`` stays a plain id list for older clients; ``items`` carries PnL/dates/bars.
    """
    if not BACKTESTS_DIR.is_dir():
        return {"runs": [], "items": []}
    items: list[dict[str, Any]] = []
    for p in BACKTESTS_DIR.iterdir():
        if not p.is_dir():
            continue
        row = _backtest_list_item(p)
        if row:
            items.append(row)
    items.sort(key=lambda r: float(r.get("sort_ts") or 0.0), reverse=True)
    return {"runs": [str(r["run_id"]) for r in items], "items": items}
