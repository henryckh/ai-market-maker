"""Weighted convergence arbitration schemas and factor maps."""

from __future__ import annotations

from dataclasses import dataclass, field

from agents.registry import get_registry


@dataclass
class FactorSignal:
    factor_id: str
    agent_id: str
    raw_value: float
    normalized: float
    weight: float
    enabled: bool = True
    source_field: str = ""


@dataclass
class AgentWeightedSignal:
    agent_id: str
    agent_type: str
    label: str
    composite: float
    raw_composite: float
    agent_weight: float
    weighted_composite: float
    factor_signals: list[FactorSignal] = field(default_factory=list)
    enabled: bool = True
    confidence: float = 0.5
    stance: str = "neutral"


@dataclass
class ArbitrationResult:
    composite_score: float
    confidence: float
    stance: str
    conviction_level: str
    reasons: list[str] = field(default_factory=list)
    agent_signals: list[AgentWeightedSignal] = field(default_factory=list)
    consensus_ratio: float = 0.0
    buy_triggered: bool = False
    sell_triggered: bool = False
    hold_triggered: bool = True
    alignment_gated: bool = False
    alignment_reason: str = ""


def _registry_maps() -> tuple[dict[str, float], dict[str, str]]:
    reg = get_registry()
    return reg.default_weights(), reg.labels()


AGENT_FACTOR_MAP: dict[str, dict[str, float]] = {
    "monetary_sentinel": {},
    "news_narrative_miner": {
        "sentiment_score": 0.28,
        "impact_score": 0.38,
        "event_type": 0.19,
        "narrative_freshness": 0.15,
    },
    "pattern_recognition_bot": {
        "setup_score": 0.40,
        "pattern_quality": 0.30,
        "timeframe_align": 0.20,
        "volume_conf": 0.10,
    },
    "statistical_alpha_engine": {
        "alpha_signal": 0.50,
        "z_score": 0.25,
        "regime_fit": 0.25,
    },
    "technical_ta_engine": {
        "rsi": 0.10,
        "macd": 0.16,
        "obv": 0.08,
        "atr": 0.05,
        "adx": 0.12,
        "ema_cross": 0.14,
        "price_momentum": 0.18,
        "roc": 0.10,
        "volume": 0.07,
    },
    "retail_hype_tracker": {
        "fomo_level": 0.35,
        "social_volume": 0.25,
        "divergence_warning": 0.40,
    },
    "pro_bias_analyst": {
        "etf_trend": 0.40,
        "funding_rate": 0.30,
        "oi_delta": 0.30,
    },
    "whale_behavior_analyst": {
        "dump_probability": 0.50,
        "concentration_pct": 0.25,
        "wallet_flow": 0.25,
    },
    "liquidity_order_flow": {
        "slippage_risk": 0.35,
        "order_imbalance": 0.35,
        "depth_skew": 0.30,
    },
}

AGENT_WEIGHTS_DEFAULT, AGENT_LABEL_MAP = _registry_maps()
AGENT_TYPE_MAP: dict[str, str] = {name: name for name in AGENT_FACTOR_MAP}


__all__ = [
    "AGENT_FACTOR_MAP",
    "AGENT_LABEL_MAP",
    "AGENT_TYPE_MAP",
    "AGENT_WEIGHTS_DEFAULT",
    "AgentWeightedSignal",
    "ArbitrationResult",
    "FactorSignal",
]
