"""Low-latency read helpers for MCP metrics / signal snapshots.

Never runs LangGraph or backtests. Prefer pre-published caches under
``.runs/mcp/{strategy_id}/``; fall back to reading existing backtest artifacts.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from api.mcp_bindings import McpBinding
from api.safe_ids import path_under, require_safe_id
from config.runs_paths import runs_dir

LISTING_SHARPE_MIN = 2.0
LISTING_PERIOD_DAYS_MIN = 30
LISTING_AUM_USDT_MIN = 10_000.0


def mcp_strategy_dir(strategy_id: str) -> Path:
    sid = require_safe_id(strategy_id, name="strategy_id")
    return path_under(runs_dir() / "mcp", sid)


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def _backtest_dir(run_id: str) -> Path:
    rid = require_safe_id(run_id, name="run_id")
    return path_under(runs_dir() / "backtests", rid)


def _trading_period_days(summary: dict[str, Any]) -> int:
    start_ts = summary.get("start_ts")
    end_ts = summary.get("end_ts")
    try:
        if start_ts is not None and end_ts is not None:
            # ms or sec
            s = float(start_ts)
            e = float(end_ts)
            if s > 1e12:
                s /= 1000.0
                e /= 1000.0
            days = max(0, int(round((e - s) / 86400.0)))
            if days > 0:
                return days
    except (TypeError, ValueError):
        pass
    eval_bars = summary.get("eval_bars") or summary.get("total_bars")
    interval = summary.get("bar_interval_sec_inferred") or summary.get("interval_sec") or 86400
    try:
        bars = int(eval_bars or 0)
        sec = float(interval or 86400)
        if bars > 0 and sec > 0:
            return max(1, int(round(bars * sec / 86400.0)))
    except (TypeError, ValueError):
        pass
    return 0


def _listing_status(*, sharpe: float, period_days: int, aum: float) -> str:
    if (
        sharpe > LISTING_SHARPE_MIN
        and period_days > LISTING_PERIOD_DAYS_MIN
        and aum > LISTING_AUM_USDT_MIN
    ):
        return "QUALIFIED_FOR_OKX_LISTING"
    return "NOT_QUALIFIED"


def metrics_from_summary(
    summary: dict[str, Any],
    *,
    estimated_aum_usdt: float | None = None,
) -> dict[str, Any]:
    metrics = summary.get("metrics") if isinstance(summary.get("metrics"), dict) else {}
    sharpe_raw = metrics.get("sharpe_ratio", metrics.get("sharpe"))
    try:
        sharpe = float(sharpe_raw) if sharpe_raw is not None else 0.0
    except (TypeError, ValueError):
        sharpe = 0.0

    pf_raw = metrics.get("profit_factor")
    try:
        profit_factor = float(pf_raw) if pf_raw is not None else None
    except (TypeError, ValueError):
        profit_factor = None

    dd_raw = metrics.get("max_drawdown_pct")
    try:
        dd_pct = float(dd_raw) if dd_raw is not None else None
    except (TypeError, ValueError):
        dd_pct = None
    max_drawdown = f"{dd_pct:.2f}%" if dd_pct is not None else None

    period_days = _trading_period_days(summary)
    if estimated_aum_usdt is not None:
        aum = float(estimated_aum_usdt)
    else:
        try:
            aum = float(summary.get("initial_cash") or 0.0)
        except (TypeError, ValueError):
            aum = 0.0

    ret_raw = metrics.get("total_return_pct")
    try:
        total_return_pct = float(ret_raw) if ret_raw is not None else None
    except (TypeError, ValueError):
        total_return_pct = None
    wr_raw = metrics.get("win_rate_pct")
    try:
        win_rate_pct = float(wr_raw) if wr_raw is not None else None
    except (TypeError, ValueError):
        win_rate_pct = None
    try:
        trade_count = int(metrics.get("total_trades") or summary.get("trade_count") or 0)
    except (TypeError, ValueError):
        trade_count = 0

    return {
        "sharpe_ratio": round(sharpe, 4),
        "trading_period_days": int(period_days),
        "estimated_aum_usdt": round(aum, 2),
        "profit_factor": round(profit_factor, 4) if profit_factor is not None else None,
        "max_drawdown": max_drawdown,
        "total_return_pct": round(total_return_pct, 4) if total_return_pct is not None else None,
        "win_rate_pct": round(win_rate_pct, 2) if win_rate_pct is not None else None,
        "trade_count": trade_count,
        "status": _listing_status(sharpe=sharpe, period_days=period_days, aum=aum),
        "source_run_id": summary.get("run_id"),
        "symbols": summary.get("symbols") or [],
    }


def _action_to_intent(action: str | None, target_weight: Any = None) -> str:
    a = (action or "").strip().upper()
    if a in {"BUY", "LONG", "ENTER_LONG"}:
        return "BUY"
    if a in {"SELL", "SHORT", "ENTER_SHORT"}:
        return "SELL"
    if a in {"HOLD", "FLAT", "CLOSE", "EXIT"}:
        return "HOLD"
    try:
        w = float(target_weight)
        if w > 0:
            return "BUY"
        if w < 0:
            return "SELL"
    except (TypeError, ValueError):
        pass
    return "HOLD"


def _reasoning_from_iteration(row: dict[str, Any]) -> str:
    desk = row.get("desk_scores") if isinstance(row.get("desk_scores"), dict) else {}
    weights = (
        row.get("deploy_profile_weights")
        if isinstance(row.get("deploy_profile_weights"), dict)
        else {}
    )
    parts: list[str] = []
    for desk_id, scores in desk.items():
        if not isinstance(scores, dict) or not scores:
            continue
        w = weights.get(desk_id)
        try:
            w_f = float(w) if w is not None else None
        except (TypeError, ValueError):
            w_f = None
        label = desk_id.replace("_", " ")
        if w_f is not None and w_f > 0:
            parts.append(f"{label}×{w_f:.2f}")
        else:
            parts.append(label)
        if len(parts) >= 4:
            break
    stance = str(row.get("stance") or "").strip()
    mode = str(row.get("arbitrator_mode") or "").strip()
    if parts:
        joined = ", ".join(parts)
        suffix = f" via {mode}" if mode else ""
        stance_bit = f" ({stance})" if stance else ""
        return f"{joined} signals aligned{stance_bit}{suffix}."
    if stance:
        return f"Latest posture {stance} from cached desk tick."
    return "Cached signal snapshot (no live inference)."


def signal_from_iteration(row: dict[str, Any], *, symbol: str) -> dict[str, Any]:
    sym = str(row.get("symbol") or symbol or "BTC/USDT")
    intent = _action_to_intent(str(row.get("action") or ""), row.get("target_weight"))
    ts_raw = row.get("ts")
    try:
        ts = int(float(ts_raw)) if ts_raw is not None else int(time.time())
    except (TypeError, ValueError):
        ts = int(time.time())
    return {
        "symbol": sym,
        "trade_intent": intent,
        "reasoning_log": _reasoning_from_iteration(row),
        "timestamp": ts,
        "confidence": row.get("confidence"),
        "composite_score": row.get("composite_score"),
        "source_run_id": row.get("run_id"),
        "bar_index": row.get("bar_index"),
    }


def _tail_jsonl(path: Path, *, max_lines: int = 200) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    rows: list[dict[str, Any]] = []
    for line in text.splitlines()[-max_lines:]:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            rows.append(obj)
    return rows


def latest_iteration_for_symbol(run_id: str, symbol: str) -> dict[str, Any] | None:
    path = _backtest_dir(run_id) / "iterations.jsonl"
    want = (symbol or "").strip().upper().replace("-", "/")
    rows = _tail_jsonl(path)
    if not rows:
        return None
    matched = [
        r
        for r in rows
        if str(r.get("symbol") or "").strip().upper().replace("-", "/") == want or not want
    ]
    pool = matched or rows
    return pool[-1] if pool else None


def write_metrics_cache(strategy_id: str, metrics: dict[str, Any]) -> Path:
    root = mcp_strategy_dir(strategy_id)
    root.mkdir(parents=True, exist_ok=True)
    dest = root / "metrics.json"
    payload = {**metrics, "cached_at": int(time.time())}
    dest.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return dest


def write_signal_cache(strategy_id: str, signal: dict[str, Any]) -> Path:
    root = mcp_strategy_dir(strategy_id)
    root.mkdir(parents=True, exist_ok=True)
    dest = root / "signal.json"
    payload = {**signal, "cached_at": int(time.time())}
    dest.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    # Also append to trades.jsonl for audit / multi-symbol history
    trades = root / "trades.jsonl"
    with trades.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    return dest


def publish_strategy_run(
    *,
    strategy_id: str,
    run_id: str,
    symbol: str = "BTC/USDT",
    estimated_aum_usdt: float | None = None,
) -> dict[str, Any]:
    """Ops path: write MCP caches from a Flow backtest without a user API key."""
    sid = (strategy_id or "").strip()
    rid = (run_id or "").strip()
    if not sid:
        raise ValueError("strategy_id required to publish MCP caches")
    if not rid:
        raise ValueError("run_id required to publish MCP caches")
    stub = McpBinding(
        api_key="",
        strategy_id=sid,
        run_id=rid,
        estimated_aum_usdt=estimated_aum_usdt,
    )
    return publish_from_run(stub, run_id=rid, symbol=symbol)


def publish_from_run(
    binding: McpBinding,
    *,
    run_id: str | None = None,
    symbol: str = "BTC/USDT",
) -> dict[str, Any]:
    rid = (run_id or binding.run_id or "").strip()
    if not rid:
        raise ValueError("run_id required to publish MCP caches")
    summary = _read_json(_backtest_dir(rid) / "summary.json")
    if not summary:
        raise FileNotFoundError(f"summary.json missing for run_id={rid}")
    metrics = metrics_from_summary(summary, estimated_aum_usdt=binding.estimated_aum_usdt)
    write_metrics_cache(binding.strategy_id, metrics)

    row = latest_iteration_for_symbol(rid, symbol)
    if row:
        signal = signal_from_iteration(row, symbol=symbol)
    else:
        signal = {
            "symbol": symbol,
            "trade_intent": "HOLD",
            "reasoning_log": "No iteration rows yet; default HOLD.",
            "timestamp": int(time.time()),
            "source_run_id": rid,
        }
    write_signal_cache(binding.strategy_id, signal)
    dest = mcp_strategy_dir(binding.strategy_id)
    dest.mkdir(parents=True, exist_ok=True)
    eq_rows = _tail_jsonl(_backtest_dir(rid) / "equity.jsonl", max_lines=2000)
    if eq_rows:
        step = max(1, len(eq_rows) // 120)
        slim_eq = [
            {"t": r.get("ts"), "equity": r.get("equity")}
            for r in eq_rows[::step][-120:]
            if isinstance(r, dict)
        ]
        (dest / "equity.json").write_text(
            json.dumps({"run_id": rid, "points": slim_eq}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    bt_trades = _tail_jsonl(_backtest_dir(rid) / "trades.jsonl", max_lines=80)
    if bt_trades:
        (dest / "fills.json").write_text(
            json.dumps({"run_id": rid, "trades": bt_trades[-50:]}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    return {"metrics": metrics, "signal": signal, "run_id": rid}


def get_strategy_metrics(binding: McpBinding) -> dict[str, Any]:
    cached = _read_json(mcp_strategy_dir(binding.strategy_id) / "metrics.json")
    if cached:
        # Strip internal cache metadata for tool response shape
        return {
            "sharpe_ratio": cached.get("sharpe_ratio"),
            "trading_period_days": cached.get("trading_period_days"),
            "estimated_aum_usdt": cached.get("estimated_aum_usdt"),
            "profit_factor": cached.get("profit_factor"),
            "max_drawdown": cached.get("max_drawdown"),
            "total_return_pct": cached.get("total_return_pct"),
            "win_rate_pct": cached.get("win_rate_pct"),
            "trade_count": cached.get("trade_count"),
            "status": cached.get("status"),
        }

    rid = (binding.run_id or "").strip()
    if not rid:
        raise FileNotFoundError(
            f"No metrics cache for strategy_id={binding.strategy_id} and no run_id binding"
        )
    summary = _read_json(_backtest_dir(rid) / "summary.json")
    if not summary:
        raise FileNotFoundError(f"summary.json missing for run_id={rid}")
    full = metrics_from_summary(summary, estimated_aum_usdt=binding.estimated_aum_usdt)
    return {
        "sharpe_ratio": full["sharpe_ratio"],
        "trading_period_days": full["trading_period_days"],
        "estimated_aum_usdt": full["estimated_aum_usdt"],
        "profit_factor": full["profit_factor"],
        "max_drawdown": full["max_drawdown"],
        "total_return_pct": full.get("total_return_pct"),
        "win_rate_pct": full.get("win_rate_pct"),
        "trade_count": full.get("trade_count"),
        "status": full["status"],
    }


def get_strategy_signal(binding: McpBinding, *, symbol: str) -> dict[str, Any]:
    want = (symbol or "").strip()
    if not want:
        raise ValueError("symbol is required")

    cached = _read_json(mcp_strategy_dir(binding.strategy_id) / "signal.json")
    if cached:
        cached_sym = str(cached.get("symbol") or "").strip().upper().replace("-", "/")
        want_n = want.upper().replace("-", "/")
        if not cached_sym or cached_sym == want_n:
            return {
                "symbol": cached.get("symbol") or want,
                "trade_intent": cached.get("trade_intent") or "HOLD",
                "reasoning_log": cached.get("reasoning_log") or "",
                "timestamp": int(cached.get("timestamp") or time.time()),
                "confidence": cached.get("confidence"),
            }

    # Tail trades.jsonl for matching symbol
    trades_path = mcp_strategy_dir(binding.strategy_id) / "trades.jsonl"
    for row in reversed(_tail_jsonl(trades_path, max_lines=500)):
        row_sym = str(row.get("symbol") or "").strip().upper().replace("-", "/")
        if row_sym == want.upper().replace("-", "/"):
            return {
                "symbol": row.get("symbol") or want,
                "trade_intent": row.get("trade_intent") or "HOLD",
                "reasoning_log": row.get("reasoning_log") or "",
                "timestamp": int(row.get("timestamp") or time.time()),
            }

    rid = (binding.run_id or "").strip()
    if rid:
        row = latest_iteration_for_symbol(rid, want)
        if row:
            sig = signal_from_iteration(row, symbol=want)
            return {
                "symbol": sig["symbol"],
                "trade_intent": sig["trade_intent"],
                "reasoning_log": sig["reasoning_log"],
                "timestamp": sig["timestamp"],
            }

    return {
        "symbol": want,
        "trade_intent": "HOLD",
        "reasoning_log": "No cached signal; default HOLD.",
        "timestamp": int(time.time()),
    }


def get_strategy_equity(binding: McpBinding) -> dict[str, Any]:
    cached = _read_json(mcp_strategy_dir(binding.strategy_id) / "equity.json")
    if cached and isinstance(cached.get("points"), list):
        return {"run_id": cached.get("run_id"), "points": cached["points"]}
    rid = (binding.run_id or "").strip()
    if not rid:
        raise FileNotFoundError("No equity cache and no run_id binding")
    rows = _tail_jsonl(_backtest_dir(rid) / "equity.jsonl", max_lines=2000)
    if not rows:
        raise FileNotFoundError(f"equity.jsonl missing for run_id={rid}")
    step = max(1, len(rows) // 120)
    points = [
        {"t": r.get("ts"), "equity": r.get("equity")}
        for r in rows[::step][-120:]
        if isinstance(r, dict)
    ]
    return {"run_id": rid, "points": points}


def get_strategy_trades(binding: McpBinding) -> dict[str, Any]:
    cached = _read_json(mcp_strategy_dir(binding.strategy_id) / "fills.json")
    if cached and isinstance(cached.get("trades"), list):
        return {"run_id": cached.get("run_id"), "trades": cached["trades"][-50:]}
    rid = (binding.run_id or "").strip()
    if not rid:
        raise FileNotFoundError("No fills cache and no run_id binding")
    rows = _tail_jsonl(_backtest_dir(rid) / "trades.jsonl", max_lines=80)
    return {"run_id": rid, "trades": rows[-50:]}
