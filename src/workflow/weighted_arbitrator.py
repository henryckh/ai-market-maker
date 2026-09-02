"""Weighted Convergence Arbitrator — LangGraph node.

This module implements the ``weighted_convergence`` arbitrator mode specified
in the v4 AI-MM config. It replaces the legacy "signal_arbitrator" LLM path
with a deterministic weighted formula:

  composite   = Σ(agent_weight × Σ(factor_weight × factor_signal_normalized))
  confidence  = |composite_magnitude| × min(1.0, 0.5 + consensus_ratio × 0.5)

The node is a drop-in replacement for ``signal_arbitrator_llm`` in the LangGraph
workflow. It reads Tier-0 contracts from the state, computes weighted signals,
applies decision thresholds, and produces a ``proposed_signal`` + ``trade_intent``
identical in shape to the LLM path output.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from threading import Lock
from typing import Any

from llm.agent_llm_client import (
    check_api_key,
    infer_agent,
    infer_arbitrator_decision,
)
from schemas.arbitration import ArbitrationResult
from schemas.state import HedgeFundState
from schemas.tier0_contract import tier0_contracts_by_agent
from workflow.execution_intent import derive_trade_intent
from workflow.tier2_context import build_synthesis_board
from workflow.weight_assigner import compute_weighted_arbitration

logger = logging.getLogger(__name__)

# Neutral permissive fallback thresholds — intentionally wide-open.
# The LLM arbitrator (when enabled) is the real decision-maker, receiving all
# agent scores and assessing regime.  When LLM is disabled, the wide gates let
# the composite signal through without static long/short bias.
_V4_DECISION_THRESHOLD: dict[str, Any] = {
    "buy": {"min_composite": 51, "min_confidence": 1},
    "sell": {"max_composite": 49, "min_confidence": 1},
    "hold": {"else": True},
    "alignment_gating": {
        "enabled": True,
        "min_factors_for_directional": 1,
        "risk_override_if_blocked": True,
    },
    "ta_led": {
        "enabled": True,
        "agent_id": "technical_ta_engine",
        "buy_min_composite": 51,
        "sell_max_composite": 49,
        "min_confidence": 1,
    },
}


def _resolve_decision_threshold(state: HedgeFundState) -> dict[str, Any]:
    override = state.get("decision_threshold")
    if isinstance(override, dict) and override:
        return dict(override)
    return dict(_V4_DECISION_THRESHOLD)


def _default_agent_weights() -> dict[str, float]:
    from agents.registry import get_registry

    return {k: v for k, v in get_registry().default_weights().items() if v > 0}


def _reasoning_entry(
    *,
    node: str,
    thought: str,
    decision: Any | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    detail = thought.strip() if isinstance(thought, str) else str(thought)
    return {
        "node": node,
        "reasoning_chain": detail,
        "thought_process": detail,
        "decision": decision,
        "extra": extra or {},
    }


def _compact_arbitration_for_reasoning(
    result: ArbitrationResult,
) -> dict[str, Any]:
    """Compact arbitration result for reasoning_logs."""
    return {
        "composite_score": result.composite_score,
        "confidence": result.confidence,
        "stance": result.stance,
        "conviction_level": result.conviction_level,
        "consensus_ratio": result.consensus_ratio,
        "buy_triggered": result.buy_triggered,
        "sell_triggered": result.sell_triggered,
        "hold_triggered": result.hold_triggered,
        "alignment_gated": result.alignment_gated,
        "reasons": result.reasons[:8],
        "agents": [
            {
                "id": s.agent_id,
                "label": s.label,
                "composite": s.composite,
                "weighted_contribution": round(s.weighted_composite, 4),
                "stance": s.stance,
                "confidence": round(s.confidence, 3),
                "enabled": s.enabled,
            }
            for s in result.agent_signals[:12]
        ],
    }


def _arbitration_to_proposed_signal(
    result: ArbitrationResult,
    state: HedgeFundState,
) -> dict[str, Any]:
    """Map ArbitrationResult → proposed_signal (same shape as LLM path)."""
    # Only pass directional stance when arbitration explicitly triggered BUY/SELL.
    # Raw composite stance can be bearish/bullish while gates still say HOLD.
    if result.buy_triggered:
        stance = "bullish"
    elif result.sell_triggered:
        stance = "bearish"
    else:
        stance = "neutral"
    confidence = result.confidence

    return {
        "action": "PROPOSAL",
        "params": {
            "stance": stance,
            "confidence": round(confidence, 4),
            "reasons": result.reasons[:12],
            "tool_events": [],
            "debate_entries": 0,
            "weighted_arbitrator": True,
            "composite_score": result.composite_score,
            "conviction_level": result.conviction_level,
            "consensus_ratio": result.consensus_ratio,
            "alignment_gated": result.alignment_gated,
            "agent_signals": [
                {
                    "agent_id": s.agent_id,
                    "label": s.label,
                    "composite": s.composite,
                    "agent_weight": s.agent_weight,
                    "weighted_composite": round(s.weighted_composite, 4),
                    "stance": s.stance,
                    "confidence": round(s.confidence, 3),
                    "factor_contributions": round(
                        sum(f.weight * f.normalized for f in s.factor_signals)
                        / max(0.001, sum(f.weight for f in s.factor_signals)),
                        4,
                    )
                    if s.factor_signals
                    else 0.5,
                }
                for s in result.agent_signals[:12]
            ],
        },
        "meta": {
            "source": "weighted_arbitrator",
            "mode": _resolve_arbitrator_mode(state),
        },
    }


def _resolve_agent_weights(state: HedgeFundState) -> dict[str, float]:
    """Deploy / profile weights only — do not merge leftover registry defaults."""
    profile = state.get("profile_weights") or {}
    if isinstance(profile, dict) and profile:
        weights = {str(k): float(v) for k, v in profile.items() if float(v or 0) > 0}
        total = sum(weights.values())
        if total > 0:
            return {k: round(v / total, 4) for k, v in weights.items()}
        return weights
    try:
        from config.deploy_loader import get_effective_weights

        ew = get_effective_weights()
        if ew:
            weights = {k: float(v) for k, v in ew.items() if float(v or 0) > 0}
            total = sum(weights.values())
            if total > 0:
                return {k: round(v / total, 4) for k, v in weights.items()}
    except (ImportError, TypeError, ValueError):
        pass
    return _default_agent_weights()


def _resolve_arbitrator_mode(state: HedgeFundState) -> str:
    """``agent_llm`` or ``weighted_convergence`` from state, then deploy JSON."""
    if "use_llm_synthesis" in state:
        return "agent_llm" if state.get("use_llm_synthesis") else "weighted_convergence"
    mode = str(state.get("arbitrator_mode") or "").strip().lower()
    if mode in ("agent_llm", "llm", "full_agentic"):
        return "agent_llm"
    if mode in ("weighted_convergence", "weighted", "measurement"):
        return "weighted_convergence"

    try:
        from config.deploy_loader import get_use_llm_synthesis

        use_llm = get_use_llm_synthesis()
        if use_llm is True:
            return "agent_llm"
        if use_llm is False:
            return "weighted_convergence"
    except Exception:
        pass

    return "weighted_convergence"


def _execution_llm_flag(state: HedgeFundState, name: str) -> bool:
    if name in state:
        return bool(state.get(name))
    try:
        from config import deploy_loader

        getter = getattr(deploy_loader, f"get_{name}", None)
        if callable(getter):
            return bool(getter())
    except Exception:
        pass
    return False


def _apply_llm_arbitration(
    state: HedgeFundState, result: ArbitrationResult
) -> tuple[ArbitrationResult, dict[str, Any] | None]:
    if not _execution_llm_flag(state, "arbitrator_llm"):
        return result, None
    if check_api_key():
        logger.warning("arbitrator_llm requested but no API key; using weighted math")
        return result, None
    overlay = infer_arbitrator_decision(
        _compact_arbitration_for_reasoning(result),
        dict(state),
        ticker=str(state.get("ticker") or ""),
    )
    if overlay.get("source") != "agent_llm":
        return result, overlay
    action = str(overlay.get("action") or "HOLD").upper()
    reasons = list(result.reasons)
    reasons.extend(str(r) for r in (overlay.get("reasons") or []) if r)
    if result.alignment_gated and action in ("BUY", "SELL"):
        reasons.append("alignment_gated: LLM directional blocked")
        action = "HOLD"
    stance = str(overlay.get("stance") or result.stance)
    conf = float(
        overlay.get("confidence") if overlay.get("confidence") is not None else result.confidence
    )
    return (
        replace(
            result,
            stance=stance,
            confidence=conf,
            reasons=reasons[:16],
            buy_triggered=action == "BUY",
            sell_triggered=action == "SELL",
            hold_triggered=action == "HOLD",
        ),
        overlay,
    )


def _get_llm_enabled_agents(state: HedgeFundState) -> list[str]:
    """Agents allowed to call LLM — from deploy JSON, else weighted desks in state."""
    try:
        from config.deploy_loader import get_llm_enabled_agent_names

        names = get_llm_enabled_agent_names()
        if names is not None:
            return list(names)
    except Exception:
        pass

    profile = state.get("profile_weights") or {}
    if isinstance(profile, dict) and profile:
        return [str(aid) for aid, w in profile.items() if float(w or 0) > 0]

    try:
        from config.deploy_loader import get_effective_weights

        ew = get_effective_weights()
        if ew:
            return [k for k, w in ew.items() if float(w or 0) > 0]
    except Exception:
        pass

    return []


def _inject_llm_signals(state: HedgeFundState) -> tuple[HedgeFundState, list[dict[str, Any]]]:
    """Parallel LLM inference for agent_llm mode.

    Returns local state for arbitration and append-only tier0 contract deltas.
    """
    mode = _resolve_arbitrator_mode(state)
    if mode != "agent_llm":
        return state, []

    key_err = check_api_key()
    if key_err:
        raise RuntimeError(f"arbitrator_mode=agent_llm but no LLM API key: {key_err}")

    llm_agents = _get_llm_enabled_agents(state)
    if not llm_agents:
        return state, []

    logger.debug(
        "agent_llm mode: running LLM inference for %d agents: %s",
        len(llm_agents),
        llm_agents,
    )
    deterministic = tier0_contracts_by_agent(state)
    primary_ticker = state.get("ticker", "")

    results: list[dict[str, Any] | None] = [None] * len(llm_agents)
    lock = Lock()

    def _run_one(idx: int, agent_id: str) -> None:
        """Run LLM inference for a single agent and store the result."""
        det_contract = deterministic.get(agent_id)
        try:
            llm_result = infer_agent(
                agent_id,
                dict(state),
                deterministic_contract=det_contract,
                ticker=primary_ticker,
            )
            with lock:
                results[idx] = dict(llm_result)
        except Exception as e:
            logger.error("agent_llm: inference failed for agent %s: %s", agent_id, e)
            if "API key" in str(e):
                raise
            error_signal = {
                "agent": agent_id,
                "agent_id": agent_id,
                "source": "error",
                "llm_enabled": True,
                "llm_error": str(e),
                "composite": 50,
                "confidence": 0.0,
            }
            with lock:
                results[idx] = error_signal

    n_workers = min(len(llm_agents), 9)
    with ThreadPoolExecutor(max_workers=n_workers) as pool:
        futures = [pool.submit(_run_one, i, aid) for i, aid in enumerate(llm_agents)]
        for future in as_completed(futures):
            exc = future.exception()
            if exc is not None and "API key" in str(exc):
                raise exc

    llm_deltas: list[dict[str, Any]] = []
    for i, agent_id in enumerate(llm_agents):
        result = results[i]
        if result is None:
            continue
        llm_deltas.append(result)
        if str(result.get("source") or "") == "error":
            # Keep the deterministic desk contract. Replacing it with a
            # neutral error stub zeros TA and can flip the bar's trade.
            continue
        tier0 = list(state.get("tier0_contracts") or [])
        replaced = False
        for j, c in enumerate(tier0):
            if not isinstance(c, dict):
                continue
            aid = c.get("agent_id") or c.get("agent", "")
            if aid == agent_id:
                tier0[j] = result
                replaced = True
                break
        if not replaced:
            tier0.append(result)
        state = dict(state)
        state["tier0_contracts"] = tier0

    state["arbitrator_mode"] = "agent_llm"
    return state, llm_deltas


def weighted_arbitrator_node(state: HedgeFundState) -> dict[str, Any]:
    """LangGraph node: weighted convergence arbitrator (supports agent_llm mode).

    Reads:
      - ``tier0_contracts`` from state
      - ``profile_weights`` (optional) — personalised weights from Profile Agent
      - ``run_mode`` for context

    Writes:
      - ``proposed_signal`` — same shape as ``signal_arbitrator_llm``
      - ``trade_intent``   — derived via ``derive_trade_intent``
      - ``reasoning_logs`` — per-agent scores + final decision
    """
    state, llm_deltas = _inject_llm_signals(state)

    agent_weights = _resolve_agent_weights(state)
    profile_id = state.get("profile_id") or ""

    idx = tier0_contracts_by_agent(state)
    if not idx:
        result = ArbitrationResult(
            composite_score=0.5,
            confidence=0.0,
            stance="neutral",
            conviction_level="none",
            reasons=["weighted_arbitrator: no Tier-0 contracts available"],
            agent_signals=[],
        )
        arb_overlay = None
    else:
        result = compute_weighted_arbitration(
            state,
            agent_weights=agent_weights,
            decision_threshold=_resolve_decision_threshold(state),
        )
        result, arb_overlay = _apply_llm_arbitration(state, result)

    proposed_signal = _arbitration_to_proposed_signal(result, state)
    if arb_overlay and arb_overlay.get("source") == "agent_llm":
        params = dict(proposed_signal.get("params") or {})
        params["llm_arbitrator"] = True
        proposed_signal["params"] = params
        meta = dict(proposed_signal.get("meta") or {})
        meta["source"] = "weighted_arbitrator+llm"
        proposed_signal["meta"] = meta

    # ---- Collect tool events from LLM agent contracts for audit trail ----
    all_tool_events: list[dict[str, Any]] = []
    for c in state.get("tier0_contracts") or []:
        if not isinstance(c, dict):
            continue
        te = c.get("_tool_events")
        if isinstance(te, list) and te:
            for evt in te:
                if isinstance(evt, dict):
                    all_tool_events.append(evt)
    if all_tool_events:
        params = dict(proposed_signal.get("params") or {})
        params["tool_events"] = all_tool_events
        proposed_signal["params"] = params
        logger.info(
            "agent_llm: collected %d tool events across %d agents",
            len(all_tool_events),
            len(state.get("tier0_contracts") or []),
        )

    intent = derive_trade_intent(state, proposed_signal)

    compact = _compact_arbitration_for_reasoning(result)
    board = build_synthesis_board(state)

    weight_source = "profile" if profile_id else "deploy"

    reasoning_logs = [
        _reasoning_entry(
            node="signal_arbitrator",
            thought=(
                f"Weighted convergence arbitration complete. "
                f"Stance={result.stance}, composite={result.composite_score:.4f}, "
                f"confidence={result.confidence:.4f}, conviction={result.conviction_level}."
                + (
                    f" LLM arbitrator: {arb_overlay.get('action')} ({arb_overlay.get('stance')})."
                    if arb_overlay and arb_overlay.get("source") == "agent_llm"
                    else ""
                )
            ),
            decision=compact,
            extra={
                "arbitrator_mode": _resolve_arbitrator_mode(state),
                "weight_source": weight_source,
                "profile_id": profile_id if profile_id else None,
                "aligned": not result.alignment_gated,
                "buy_triggered": result.buy_triggered,
                "sell_triggered": result.sell_triggered,
                "synthesis_board_present": bool(board.get("bull_case")),
                "arbitrator_llm": bool(arb_overlay and arb_overlay.get("source") == "agent_llm"),
            },
        ),
        _reasoning_entry(
            node="execution_intent",
            thought="Execution intent derived from weighted arbitration composite.",
            decision=intent,
            extra={"weighted_arbitrator": True},
        ),
    ]

    # Per-agent reasoning for the transcript
    for sig in result.agent_signals:
        if not sig.enabled:
            continue
        factor_breakdown = {
            f.factor_id: {
                "raw": round(f.raw_value, 4),
                "normalized": round(f.normalized, 4),
                "weight": f.weight,
            }
            for f in sig.factor_signals
        }
        reasoning_logs.append(
            _reasoning_entry(
                node="signal_arbitrator",
                thought=(
                    f"Agent [{sig.agent_id}] {sig.label}: "
                    f"composite={sig.composite:.4f}, stance={sig.stance}, "
                    f"weighted_contribution={sig.weighted_composite:.4f}"
                ),
                decision={
                    "agent_id": sig.agent_id,
                    "agent_type": sig.agent_type,
                    "label": sig.label,
                    "composite": round(sig.composite, 4),
                    "agent_weight": sig.agent_weight,
                    "weighted_composite": round(sig.weighted_composite, 4),
                    "stance": sig.stance,
                    "confidence": round(sig.confidence, 3),
                    "factor_count": len(sig.factor_signals),
                    "factor_breakdown": factor_breakdown,
                },
                extra={
                    "arbitrator_mode": _resolve_arbitrator_mode(state),
                    "agent_id": sig.agent_id,
                },
            )
        )

    out: dict[str, Any] = {
        "proposed_signal": proposed_signal,
        "trade_intent": intent,
        "reasoning_logs": reasoning_logs,
        "arbitration_result": {
            "composite": result.composite_score,
            "confidence": result.confidence,
            # Gated stance (matches trade_intent), not raw composite lean.
            "stance": proposed_signal.get("params", {}).get("stance") or "neutral",
            "buy_triggered": bool(result.buy_triggered),
            "sell_triggered": bool(result.sell_triggered),
            "conviction": result.conviction_level,
            "consensus_ratio": result.consensus_ratio,
            "alignment_gated": result.alignment_gated,
        },
    }
    if llm_deltas:
        # Append-only: LangGraph ``tier0_contracts`` uses operator.add (last wins per agent).
        out["tier0_contracts"] = llm_deltas
    return out


__all__ = ["weighted_arbitrator_node"]
