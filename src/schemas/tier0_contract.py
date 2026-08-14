"""Canonical Tier-0 JSON contract for Tier-1 / strategy consumers.

Each perception node appends one object to ``HedgeFundState.tier0_contracts`` (reducer: list concat).
Field names follow the PM-facing examples (PascalCase scalars where specified).
"""

from __future__ import annotations

from typing import Any

from agents.registry import get_registry

CONTRACT_SCHEMA_VERSION = "tier0/v1"
TIER0_AGENT_NAMES: frozenset[str] = frozenset(get_registry().names())


def _f(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _macro_regime_state(liquidity_regime: str) -> int:
    m = (liquidity_regime or "neutral").lower().replace(" ", "_")
    if m in ("risk_on", "riskon", "expansion"):
        return 2
    if m in ("risk_off", "riskoff", "contraction"):
        return 0
    return 1


def _contract_monetary_sentinel(analysis: dict[str, Any], ticker: str) -> dict[str, Any]:
    score = _f(analysis.get("systemic_beta_score"), 50.0)
    regime = str(analysis.get("liquidity_regime") or "neutral")
    return {
        "schema_version": CONTRACT_SCHEMA_VERSION,
        "agent": "monetary_sentinel",
        "ticker": ticker,
        "status": str(analysis.get("status") or "success"),
        "macro_regime_state": _macro_regime_state(regime),
        "regime_prob": round(min(0.99, max(0.01, score / 100.0)), 2),
        "Liquidity_Score": int(round(min(100.0, max(0.0, score)))),
    }


def _contract_news_narrative(analysis: dict[str, Any], ticker: str) -> dict[str, Any]:
    impact = _f(analysis.get("breaker_score"), 0.0)
    if impact >= 75:
        ev = "Black Swan"
    elif impact >= 45:
        ev = "Major Catalyst"
    elif impact >= 25:
        ev = "Elevated"
    else:
        ev = "Routine"
    return {
        "schema_version": CONTRACT_SCHEMA_VERSION,
        "agent": "news_narrative_miner",
        "ticker": ticker,
        "status": str(analysis.get("status") or "success"),
        "News_Impact_Score": int(round(min(100.0, max(0.0, impact)))),
        "Event_Type": ev,
        "decay_factor": analysis.get("decay_factor"),
    }


def _contract_pattern_recognition(analysis: dict[str, Any], ticker: str) -> dict[str, Any]:
    setup = _f(analysis.get("setup_confidence_score"), 0.0)
    sr = (
        analysis.get("support_resistance")
        if isinstance(analysis.get("support_resistance"), dict)
        else {}
    )
    sup = sr.get("support") if isinstance(sr, dict) else None
    kal = None
    if isinstance(sup, (int, float)):
        kal = float(sup)
    pat = analysis.get("pattern") or analysis.get("macro_regime")
    return {
        "schema_version": CONTRACT_SCHEMA_VERSION,
        "agent": "pattern_recognition_bot",
        "ticker": ticker,
        "status": str(analysis.get("status") or "success"),
        "Setup_Score": int(round(min(100.0, max(0.0, setup)))),
        "kalman_support": kal,
        "pattern": str(pat) if pat is not None else "unknown",
    }


def _contract_statistical_alpha(analysis: dict[str, Any], ticker: str) -> dict[str, Any]:
    sig = str(analysis.get("alpha_signal") or "hold").lower()
    label_map = {
        "long_bias": "Strong Buy",
        "short_bias": "Strong Sell",
        "hold": "Hold",
    }
    alpha_label = label_map.get(sig, sig.replace("_", " ").title())
    rank = analysis.get("cross_sectional_rank")
    z = 0.0
    if analysis.get("cross_sectional_z_score") is not None:
        try:
            z = float(analysis["cross_sectional_z_score"])
        except (TypeError, ValueError):
            z = 0.0
    elif isinstance(rank, int) and rank > 0:
        z = max(-3.0, min(3.0, 3.0 - (rank - 1) * 0.15))
    conf = 50
    if isinstance(rank, int):
        if rank <= 3:
            conf = 95
        elif rank <= 10:
            conf = 75
        elif rank <= 25:
            conf = 55
    return {
        "schema_version": CONTRACT_SCHEMA_VERSION,
        "agent": "statistical_alpha_engine",
        "ticker": ticker,
        "status": str(analysis.get("status") or "success"),
        "Factor_Confluence": conf,
        "cross_sectional_z_score": round(z, 2) if analysis.get("status") == "success" else None,
        "alpha_signal": alpha_label,
    }


def _contract_technical_ta(analysis: dict[str, Any], ticker: str) -> dict[str, Any]:
    """Classical TA bundle for Tier-1 ``ta_*`` metric_ids."""
    ti = analysis.get("ta_indicators")
    if not isinstance(ti, dict):
        ti = {}
    return {
        "schema_version": CONTRACT_SCHEMA_VERSION,
        "agent": "technical_ta_engine",
        "ticker": ticker,
        "status": str(analysis.get("status") or "skipped"),
        "ta_period": analysis.get("ta_period"),
        "bars_used": analysis.get("bars"),
        "indicator_catalog_version": analysis.get("indicator_catalog_version") or "ta_bundle/v1",
        "ta_indicators": ti,
    }


def _contract_retail_hype(analysis: dict[str, Any], ticker: str) -> dict[str, Any]:
    return {
        "schema_version": CONTRACT_SCHEMA_VERSION,
        "agent": "retail_hype_tracker",
        "ticker": ticker,
        "status": str(analysis.get("status") or "success"),
        "FOMO_Level": int(min(100, max(0, int(_f(analysis.get("fomo_level"), 50))))),
        "Divergence_Warning": bool(analysis.get("divergence_warning")),
        "sentiment_z_score": round(_f(analysis.get("sentiment_z_score"), 0.0), 2),
    }


def _contract_pro_bias(analysis: dict[str, Any], ticker: str) -> dict[str, Any]:
    score = _f(analysis.get("pro_bias_score"), 50.0)
    regime = str(analysis.get("regime") or "passive_rotation").lower()
    if "accumulation" in regime:
        etf = "Accumulation"
    elif "distribution" in regime:
        etf = "Distribution"
    else:
        etf = "Neutral"
    return {
        "schema_version": CONTRACT_SCHEMA_VERSION,
        "agent": "pro_bias_analyst",
        "ticker": ticker,
        "status": str(analysis.get("status") or "success"),
        "Pro_Bias": int(round(min(100.0, max(0.0, score)))),
        "ETF_Trend": etf,
        "ema_slope": analysis.get("ema_slope"),
    }


def _contract_whale_behavior(analysis: dict[str, Any], ticker: str) -> dict[str, Any]:
    dump = _f(analysis.get("dump_probability"), 0.0)
    gauge = int(round(min(100.0, max(0.0, dump * 100.0))))
    dp = str(analysis.get("dry_powder_alert") or "unknown").lower()
    if dp in ("elevated", "high"):
        dpa = "High"
    elif dp in ("low", "thin"):
        dpa = "Low"
    else:
        dpa = "Normal"
    return {
        "schema_version": CONTRACT_SCHEMA_VERSION,
        "agent": "whale_behavior_analyst",
        "ticker": ticker,
        "status": str(analysis.get("status") or "success"),
        "Sell_Pressure_Gauge": gauge,
        "Dump_Probability": round(dump, 2),
        "Dry_Powder_Alert": dpa,
    }


def _contract_liquidity_order_flow(analysis: dict[str, Any], ticker: str) -> dict[str, Any]:
    has_depth = bool(analysis.get("nexus_depth_attached"))
    st = str(analysis.get("status") or "skipped")
    slip = analysis.get("slippage_risk_score")
    if slip is None:
        slip = 40 if has_depth and st == "success" else 85
    return {
        "schema_version": CONTRACT_SCHEMA_VERSION,
        "agent": "liquidity_order_flow",
        "ticker": ticker,
        "status": st,
        "Slippage_Risk_Score": int(slip) if isinstance(slip, (int, float)) else slip,
        "Order_Imbalance": analysis.get("order_imbalance"),
        "POC_Price": analysis.get("poc_price"),
    }


_BUILDERS: dict[str, Any] = {
    "monetary_sentinel": _contract_monetary_sentinel,
    "news_narrative_miner": _contract_news_narrative,
    "pattern_recognition_bot": _contract_pattern_recognition,
    "statistical_alpha_engine": _contract_statistical_alpha,
    "technical_ta_engine": _contract_technical_ta,
    "retail_hype_tracker": _contract_retail_hype,
    "pro_bias_analyst": _contract_pro_bias,
    "whale_behavior_analyst": _contract_whale_behavior,
    "liquidity_order_flow": _contract_liquidity_order_flow,
}


def build_tier0_contract_json(
    node_id: str, primary_analysis: dict[str, Any], ticker: str
) -> dict[str, Any]:
    fn = _BUILDERS.get(node_id)
    if fn is None:
        return {
            "schema_version": CONTRACT_SCHEMA_VERSION,
            "agent": node_id,
            "ticker": ticker,
            "status": "error",
            "error": f"unknown_tier0_node:{node_id}",
        }
    return fn(primary_analysis, ticker)


def tier0_contracts_by_agent(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in state.get("tier0_contracts") or []:
        if not isinstance(row, dict):
            continue
        aid = row.get("agent") or row.get("agent_id")
        if aid:
            out[str(aid)] = row
    return out


def tier0_consensus_for_arbitrator(state: dict[str, Any]) -> dict[str, Any]:
    """Aggregate Tier-0 canonical contracts into bull/bear tilts for ``signal_arbitrator``.

    Uses only Tier-0 JSON fields (no raw Nexus blobs). When ``tier0_contracts`` is empty,
    returns zero tilts and ``tier0_skipped``.
    """
    idx = tier0_contracts_by_agent(state)
    if not idx:
        return {
            "bull_tilt": 0,
            "bear_tilt": 0,
            "block_aggressive_long": False,
            "parts": [],
            "summary": "tier0_skipped",
        }

    bull = 0
    bear = 0
    block = False
    parts: list[str] = []

    m = idx.get("monetary_sentinel") or {}
    mrs = m.get("macro_regime_state")
    if mrs == 2:
        bull += 1
        parts.append("macro_risk_on")
    elif mrs == 0:
        bear += 1
        parts.append("macro_risk_off")

    n = idx.get("news_narrative_miner") or {}
    try:
        ni = int(n.get("News_Impact_Score") or 0)
    except (TypeError, ValueError):
        ni = 0
    et = str(n.get("Event_Type") or "")
    if ni >= 80 or "Black Swan" in et:
        block = True
        bear += 2
        parts.append("news_shock")
    elif ni >= 55:
        bear += 1
        parts.append("news_elevated")

    t21 = idx.get("pattern_recognition_bot") or {}
    try:
        setup = int(t21.get("Setup_Score") or 0)
    except (TypeError, ValueError):
        setup = 0
    if setup >= 70:
        bull += 1
        parts.append("pattern_setup")

    t22 = idx.get("statistical_alpha_engine") or {}
    sig = str(t22.get("alpha_signal") or "")
    if "Strong Buy" in sig:
        bull += 1
        parts.append("stat_long")
    elif "Strong Sell" in sig:
        bear += 1
        parts.append("stat_short")

    t23 = idx.get("technical_ta_engine") or {}
    ti = t23.get("ta_indicators") if isinstance(t23.get("ta_indicators"), dict) else {}
    rsi_v = ti.get("rsi")
    try:
        rsi_f = float(rsi_v) if rsi_v is not None else None
    except (TypeError, ValueError):
        rsi_f = None
    if rsi_f is not None:
        if rsi_f >= 70.0:
            bear += 1
            parts.append("ta_rsi_stretched")
        elif rsi_f <= 30.0:
            bull += 1
            parts.append("ta_rsi_oversold")

    r31 = idx.get("retail_hype_tracker") or {}
    try:
        fomo = int(r31.get("FOMO_Level") or 0)
    except (TypeError, ValueError):
        fomo = 0
    if r31.get("Divergence_Warning") and fomo >= 85:
        bear += 1
        parts.append("retail_hype_div")

    p32 = idx.get("pro_bias_analyst") or {}
    etf = str(p32.get("ETF_Trend") or "")
    if etf == "Accumulation":
        bull += 1
        parts.append("pro_accum")
    elif etf == "Distribution":
        bear += 1
        parts.append("pro_dist")

    w41 = idx.get("whale_behavior_analyst") or {}
    try:
        dump = float(w41.get("Dump_Probability") or 0.0)
    except (TypeError, ValueError):
        dump = 0.0
    if dump >= 0.65:
        bear += 1
        parts.append("whale_dump")

    l42 = idx.get("liquidity_order_flow") or {}
    try:
        slip = int(l42.get("Slippage_Risk_Score") or 0)
    except (TypeError, ValueError):
        slip = 0
    if slip >= 80:
        bear += 1
        parts.append("flow_slip")

    return {
        "bull_tilt": bull,
        "bear_tilt": bear,
        "block_aggressive_long": block,
        "parts": parts,
        "summary": ",".join(parts) if parts else "tier0_neutral",
    }


__all__ = [
    "CONTRACT_SCHEMA_VERSION",
    "TIER0_AGENT_NAMES",
    "build_tier0_contract_json",
    "tier0_contracts_by_agent",
    "tier0_consensus_for_arbitrator",
]
