"""Default agentic desk combo for backtests when no deploy.active.json is present."""

from __future__ import annotations

import copy
from typing import Any

# macro_tilt preset (run_agentic_sweep): TA-led desk + macro/pattern support.
DEFAULT_AGENTIC_PROFILE_ID = "macro_tilt"
DEFAULT_AGENTIC_PROFILE_WEIGHTS: dict[str, float] = {
    "technical_ta_engine": 0.55,
    "pattern_recognition_bot": 0.15,
    "monetary_sentinel": 0.25,
}

# Neutral permissive thresholds — the deterministic gates are intentionally
# wide-open (buy ≥ 51, sell ≤ 49, confidence ≥ 1).  The LLM arbitrator
# (arbitrator_llm=true) is the real decision-maker.  It receives all agent
# composite scores, stances, and confidences and can assess regime (bull/bear)
# before committing to BUY/SELL/HOLD.
#
# When no LLM is available (use_llm_synthesis=false), the wide gates still
# allow the composite signal through, but with no static long/short bias.
DEFAULT_AGENTIC_DECISION_THRESHOLD: dict[str, Any] = {
    "buy": {"min_composite": 51, "min_confidence": 1},
    "sell": {"max_composite": 49, "min_confidence": 1},
    "hold": {"else": True},
    "alignment_gating": {
        "enabled": True,
        "min_factors_for_directional": 1,
        "risk_override_if_blocked": True,
    },
    "ta_led": {
        "enabled": False,
        "agent_id": "technical_ta_engine",
        "buy_min_composite": 51,
        "sell_max_composite": 49,
        "min_confidence": 1,
    },
}


def default_agentic_profile_weights() -> dict[str, float]:
    return dict(DEFAULT_AGENTIC_PROFILE_WEIGHTS)


def default_agentic_decision_threshold() -> dict[str, Any]:
    return copy.deepcopy(DEFAULT_AGENTIC_DECISION_THRESHOLD)


__all__ = [
    "DEFAULT_AGENTIC_DECISION_THRESHOLD",
    "DEFAULT_AGENTIC_PROFILE_ID",
    "DEFAULT_AGENTIC_PROFILE_WEIGHTS",
    "default_agentic_decision_threshold",
    "default_agentic_profile_weights",
]
