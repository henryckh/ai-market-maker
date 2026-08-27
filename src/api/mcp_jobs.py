"""Start / poll Flow backtests from MCP for the bound strategy_id."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from api.mcp_bindings import McpBinding, set_run_id_for_strategy
from api.mcp_cache import publish_from_run
from api.mcp_credits import (
    attach_mcp_flow_run,
    refund_mcp_credits,
    reserve_mcp_credits,
)
from api.safe_ids import require_safe_id
from config.runs_paths import runs_dir

MCP_MAX_BARS = 500


def _jobs_root() -> Path:
    return runs_dir() / "backtests"


def _job_strategy_id(job: dict[str, Any]) -> str:
    sid = str(job.get("strategy_id") or "").strip()
    if sid:
        return sid
    req = job.get("request") if isinstance(job.get("request"), dict) else {}
    return str(req.get("strategy_id") or "").strip()


def _read_job_file(path: Path) -> dict[str, Any] | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def latest_job_for_strategy(strategy_id: str) -> tuple[str, dict[str, Any]] | None:
    sid = (strategy_id or "").strip()
    if not sid:
        return None
    root = _jobs_root()
    if not root.is_dir():
        return None
    best: tuple[float, str, dict[str, Any]] | None = None
    for d in root.iterdir():
        if not d.is_dir():
            continue
        p = d / "job.json"
        if not p.is_file():
            continue
        job = _read_job_file(p)
        if not job or _job_strategy_id(job) != sid:
            continue
        mtime = p.stat().st_mtime
        if best is None or mtime > best[0]:
            best = (mtime, d.name, job)
    if best is None:
        return None
    return best[1], best[2]


def _credits_blob(reservation: dict[str, Any]) -> dict[str, Any]:
    return {
        "reservation_id": reservation.get("reservation_id"),
        "credits_reserved": int(reservation.get("credits_reserved") or 0),
        "remaining": reservation.get("remaining"),
        "refunded": False,
    }


def _write_job_patch(run_id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
    job: dict[str, Any] | None = None
    try:
        from api.backtest_routes import BACKTEST_JOBS, _write_job

        mem = BACKTEST_JOBS.get(run_id)
        if isinstance(mem, dict):
            mem.update(patch)
            _write_job(run_id, dict(mem))
            return dict(mem)
    except Exception:
        job = None
    path = _jobs_root() / run_id / "job.json"
    job = _read_job_file(path)
    if job is None:
        return None
    job.update(patch)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return job


def _should_refund_job(job: dict[str, Any]) -> bool:
    credits = job.get("mcp_credits") if isinstance(job.get("mcp_credits"), dict) else {}
    if not credits or credits.get("refunded"):
        return False
    if int(credits.get("credits_reserved") or 0) <= 0:
        return False
    status = str(job.get("status") or "")
    if status not in {"failed", "cancelled"}:
        return False
    if int(job.get("step") or 0) > 0:
        return False
    return True


def _maybe_refund_failed_job(
    binding: McpBinding, run_id: str, job: dict[str, Any]
) -> dict[str, Any]:
    if not _should_refund_job(job):
        return job
    credits = job.get("mcp_credits") if isinstance(job.get("mcp_credits"), dict) else {}
    refund_mcp_credits(binding, credits)
    return _write_job_patch(run_id, {"mcp_credits": {**credits, "refunded": True}}) or {
        **job,
        "mcp_credits": {**credits, "refunded": True},
    }


def _public_job(run_id: str, job: dict[str, Any]) -> dict[str, Any]:
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    return {
        "run_id": run_id,
        "status": job.get("status") or "unknown",
        "step": job.get("step") or 0,
        "total_steps": job.get("total_steps") or 0,
        "trade_count": job.get("trade_count") or result.get("trade_count") or 0,
        "equity": job.get("equity")
        if job.get("equity") is not None
        else result.get("final_equity"),
        "error": job.get("error"),
        "poll": f"/backtests/jobs/{run_id}",
        "hint": "Poll get_backtest_job with this run_id until status is completed or failed.",
    }


def run_backtest_for_binding(binding: McpBinding, args: dict[str, Any]) -> dict[str, Any]:
    """Re-run the last Studio-compiled deploy for this strategy (async job)."""
    prev = latest_job_for_strategy(binding.strategy_id)
    if prev is None:
        raise ValueError(
            "No compiled strategy on the engine yet. Fire a backtest once from Nexus Studio, "
            "then MCP can start and poll further runs."
        )
    _, prev_job = prev
    req = prev_job.get("request") if isinstance(prev_job.get("request"), dict) else {}
    deploy = req.get("deploy")
    if not isinstance(deploy, dict) or not deploy.get("agents"):
        raise ValueError(
            "Previous run has no deploy JSON. Fire a backtest from Nexus Studio first."
        )

    n_bars = req.get("n_bars") or 180
    if args.get("n_bars") is not None:
        try:
            n_bars = int(args["n_bars"])
        except (TypeError, ValueError) as exc:
            raise ValueError("n_bars must be an integer") from exc
    n_bars = max(20, min(MCP_MAX_BARS, n_bars))

    from api.backtest_routes import StrategyBacktestRequest, post_strategy_backtest_run

    reserved = reserve_mcp_credits(binding, n_bars=n_bars)

    payload = StrategyBacktestRequest(
        user_id=str(req.get("user_id") or binding.user_id or ""),
        strategy_id=binding.strategy_id,
        ticker=str(req.get("ticker") or "BTC/USDT"),
        symbols=str(req.get("symbols") or ""),
        n_bars=n_bars,
        interval_sec=int(req.get("interval_sec") or 3600),
        initial_cash=float(req.get("initial_cash") or 10_000),
        fee_bps=float(req.get("fee_bps") or 5),
        exchange_id=str(req.get("exchange_id") or "binance"),
        deploy=deploy,
    )
    try:
        out = post_strategy_backtest_run(payload)
    except HTTPException:
        if reserved:
            refund_mcp_credits(binding, reserved)
        raise
    except Exception as exc:
        if reserved:
            refund_mcp_credits(binding, reserved)
        raise HTTPException(
            status_code=502,
            detail={"error": "enqueue_failed", "hint": str(exc)},
        ) from exc
    rid = str(out.get("run_id") or "").strip()
    if not rid:
        if reserved:
            refund_mcp_credits(binding, reserved)
        raise ValueError("Flow did not return run_id")
    set_run_id_for_strategy(binding.strategy_id, rid)
    public: dict[str, Any] = {
        "run_id": rid,
        "status": "queued",
        "strategy_id": binding.strategy_id,
        "n_bars": n_bars,
        "poll": out.get("poll") or f"/backtests/jobs/{rid}",
        "hint": "Call get_backtest_job with this run_id until completed, then get_strategy_signal / metrics.",
    }
    if reserved:
        _write_job_patch(rid, {"mcp_credits": _credits_blob(reserved)})
        attach_mcp_flow_run(binding, reserved, rid)
        public["credits_reserved"] = int(reserved.get("credits_reserved") or 0)
        public["credits_remaining"] = reserved.get("remaining")
        public["reservation_id"] = reserved.get("reservation_id")
    return public


def get_job_for_binding(binding: McpBinding, args: dict[str, Any]) -> dict[str, Any]:
    rid = str(args.get("run_id") or args.get("job_id") or "").strip()
    if not rid:
        latest = latest_job_for_strategy(binding.strategy_id)
        if latest is None:
            raise FileNotFoundError("No backtest job for this strategy yet")
        rid, job = latest
        job = _maybe_refund_failed_job(binding, rid, job)
        return _public_job(rid, job)

    rid = require_safe_id(rid, name="run_id")
    job: dict[str, Any] | None = None
    try:
        from api.backtest_routes import BACKTEST_JOBS

        mem = BACKTEST_JOBS.get(rid)
        if isinstance(mem, dict):
            job = dict(mem)
    except Exception:
        job = None
    if job is None:
        job = _read_job_file(_jobs_root() / rid / "job.json")
    if job is None:
        raise FileNotFoundError("Unknown job or run_id")
    if _job_strategy_id(job) and _job_strategy_id(job) != binding.strategy_id:
        raise FileNotFoundError("run_id is not owned by this API key's strategy")
    job = _maybe_refund_failed_job(binding, rid, job)
    public = _public_job(rid, job)
    if str(job.get("status") or "") == "completed":
        try:
            ticker = "BTC/USDT"
            req = job.get("request") if isinstance(job.get("request"), dict) else {}
            ticker = str(req.get("ticker") or ticker)
            publish_from_run(binding, run_id=rid, symbol=ticker)
            public["published"] = True
        except Exception:
            public["published"] = False
    return public
