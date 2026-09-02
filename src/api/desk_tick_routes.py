"""One-shot desk tick with inline deploy JSON (no shared deploy.active.json)."""

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from api.tenant_deploy import write_tenant_deploy
from config.deploy_context import thread_deploy_path
from llm.usage import reset_usage, snapshot_usage

logger = logging.getLogger(__name__)
router = APIRouter(tags=["desk"])


class DeskTickRequest(BaseModel):
    ticker: str = Field("BTC/USDT", min_length=3)
    run_mode: str = Field("paper")
    user_id: str = ""
    strategy_id: str = ""
    deploy: dict[str, Any]


def _signal_from_state(state: dict[str, Any], ticker: str) -> dict[str, Any]:
    intent = state.get("trade_intent") if isinstance(state.get("trade_intent"), dict) else {}
    proposed = (
        state.get("proposed_signal") if isinstance(state.get("proposed_signal"), dict) else {}
    )
    params = proposed.get("params") if isinstance(proposed.get("params"), dict) else {}
    action = str(intent.get("action") or "HOLD").upper()
    if action not in ("BUY", "SELL", "HOLD"):
        stance = str(params.get("stance") or "neutral").lower()
        if stance in ("bullish", "long", "buy"):
            action = "BUY"
        elif stance in ("bearish", "short", "sell"):
            action = "SELL"
        else:
            action = "HOLD"
    conf = params.get("confidence")
    try:
        conf_f = float(conf) if conf is not None else 0.0
    except (TypeError, ValueError):
        conf_f = 0.0
    conf_pct = conf_f * 100.0 if conf_f <= 1.0 else conf_f
    reasons = params.get("reasons") if isinstance(params.get("reasons"), list) else []
    return {
        "action": action,
        "stance": params.get("stance") or "neutral",
        "confidence": conf_f,
        "confidence_pct": round(conf_pct, 2),
        "reasons": [str(r) for r in reasons][:12],
        "ticker": ticker,
    }


@router.post("/desk/tick")
def post_desk_tick(req: DeskTickRequest) -> dict[str, Any]:
    if not isinstance(req.deploy, dict) or not req.deploy.get("agents"):
        raise HTTPException(status_code=400, detail="deploy.agents is required")

    run_mode = "paper" if req.run_mode.lower() != "live" else "paper"
    rid = f"tick-{req.strategy_id or 'desk'}-{int(time.time())}"[:80]
    rid = "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in rid)
    deploy_path = write_tenant_deploy(deploy=req.deploy, run_id=rid, user_id=req.user_id or "anon")

    reset_usage()
    from config.agent_prompts import (
        apply_inline_prompt_overrides,
        clear_inline_prompt_overrides,
        parse_inline_prompt_rows,
    )

    prompt_rows = parse_inline_prompt_rows(req.deploy.get("agent_prompts"))
    prompt_token = apply_inline_prompt_overrides(prompt_rows) if prompt_rows else None
    try:
        from config.deploy_loader import (
            get_arbitrator_llm,
            get_arbitrator_mode,
            get_effective_weights,
        )
        from main import build_workflow
        from schemas.state import initial_hedge_fund_state

        with thread_deploy_path(deploy_path):
            weights = get_effective_weights() or {}
            state = initial_hedge_fund_state(
                run_mode=run_mode,
                ticker=req.ticker,
                profile_weights=weights or None,
            )
            arb = get_arbitrator_mode()
            if arb:
                state["arbitrator_mode"] = arb
            state["arbitrator_llm"] = get_arbitrator_llm()
            app = build_workflow().compile()
            result = app.invoke(state)
    except Exception as e:
        logger.exception("desk tick failed")
        raise HTTPException(status_code=500, detail=str(e)) from e
    finally:
        if prompt_token is not None:
            clear_inline_prompt_overrides(prompt_token)

    if not isinstance(result, dict):
        result = {}
    signal = _signal_from_state(result, req.ticker)
    tier0 = result.get("tier0_contracts") if isinstance(result.get("tier0_contracts"), list) else []
    return {
        "ok": True,
        "run_id": rid,
        "ticker": req.ticker,
        "coin": req.ticker.replace("/USDT", "").replace("/", ""),
        "is_vetoed": bool(result.get("is_vetoed")),
        "veto_reason": str(result.get("veto_reason") or ""),
        "risk_status": (result.get("risk_report") or {}).get("status")
        if isinstance(result.get("risk_report"), dict)
        else None,
        "execution_status": (result.get("execution_result") or {}).get("status")
        if isinstance(result.get("execution_result"), dict)
        else None,
        "signal": signal,
        "trade_intent": result.get("trade_intent") or {},
        "proposed_signal": result.get("proposed_signal") or {},
        "tier0_agents": tier0[-12:],
        "usage": snapshot_usage(),
    }
