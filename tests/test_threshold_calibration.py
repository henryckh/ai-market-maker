"""Threshold calibration: prove BUY/SELL are reachable with neutral permissive thresholds.

Confidence formula (from ``weight_assigner.compute_global_weighted_score``):
    magnitude = |composite - 0.50| * 2.0         # [0, 1]
    multiplier = min(1.0, 0.5 + consensus_ratio * 0.5)  # [0.5, 1.0]
    confidence = magnitude * multiplier           # [0, 1]

Thresholds (neutral, permissive — LLM arbitrator is the real gate):
    BUY:  composite >= 0.51  AND  confidence >= 0.01
    SELL: composite <= 0.49  AND  confidence >= 0.01

Tests use the **real v4 agent weights** (not uniform) to match production.
"""

from __future__ import annotations

from schemas.arbitration import AGENT_WEIGHTS_DEFAULT, AgentWeightedSignal
from workflow.weight_assigner import compute_global_weighted_score

_AGENT_IDS = list(AGENT_WEIGHTS_DEFAULT.keys())
_TOTAL_W = sum(AGENT_WEIGHTS_DEFAULT.values())


def _sig(aid: str, composite: float) -> AgentWeightedSignal:
    """Build a minimal ``AgentWeightedSignal`` with real v4 weights."""
    w = AGENT_WEIGHTS_DEFAULT.get(aid, 1.0 / 9)
    return AgentWeightedSignal(
        agent_id=aid,
        agent_type="test",
        label=aid,
        composite=composite,
        raw_composite=composite,
        agent_weight=w,
        weighted_composite=composite * w,
        enabled=True,
        confidence=abs(composite - 0.5) * 2.0,
        stance="bullish" if composite >= 0.55 else "bearish" if composite <= 0.45 else "neutral",
        factor_signals=[],
    )


def _nth_bullish(n: int, composite: float = 0.60) -> list[AgentWeightedSignal]:
    """First *n* agents bullish, rest neutral."""
    sigs = [_sig(a, composite) for a in _AGENT_IDS[:n]]
    sigs += [_sig(a, 0.50) for a in _AGENT_IDS[n:]]
    return sigs


def _nth_bearish(n: int, composite: float = 0.40) -> list[AgentWeightedSignal]:
    """First *n* agents bearish, rest neutral."""
    sigs = [_sig(a, composite) for a in _AGENT_IDS[:n]]
    sigs += [_sig(a, 0.50) for a in _AGENT_IDS[n:]]
    return sigs


# ---------------------------------------------------------------------------
# Reachability — BUY side (neutral thresholds: composite ≥ 0.51, confidence ≥ 0.01)
# ---------------------------------------------------------------------------


def test_buy_reachable_at_2_of_9_with_real_weights():
    """2/9 bullish at 0.55 with real weights → composite > 0.51, confidence ≥ 0.01 → BUY."""
    signals = _nth_bullish(2, 0.55)
    score = compute_global_weighted_score(signals)
    assert score["composite"] >= 0.51, (
        f"2/9 at 0.55: composite={score['composite']:.3f} should be >= 0.51"
    )
    assert score["confidence"] >= 0.01, (
        f"2/9 at 0.55: confidence={score['confidence']:.3f} should be >= 0.01"
    )


def test_buy_still_triggered_at_1_of_9():
    """1/9 bullish at 0.56 → still passes permissive threshold."""
    signals = _nth_bullish(1, 0.56)
    score = compute_global_weighted_score(signals)
    assert score["composite"] >= 0.51, (
        f"1/9 at 0.56: composite={score['composite']:.3f} should be >= 0.51"
    )


def test_neutral_agents_no_buy():
    """All agents neutral at 0.50 → composite stays at 0.50, below buy threshold."""
    signals = _nth_bullish(0, 0.50)
    score = compute_global_weighted_score(signals)
    assert score["composite"] < 0.51, (
        f"all neutral: composite={score['composite']:.3f} should be < 0.51"
    )


def test_buy_stronger_with_more_consensus():
    """7/9 → higher confidence than 5/9 (same agent composite)."""
    s5 = compute_global_weighted_score(_nth_bullish(5, 0.60))
    s7 = compute_global_weighted_score(_nth_bullish(7, 0.60))
    assert s7["confidence"] > s5["confidence"], (
        f"7/9 ({s7['confidence']:.3f}) > 5/9 ({s5['confidence']:.3f})"
    )


# ---------------------------------------------------------------------------
# Reachability — SELL side (symmetric: composite ≤ 0.49, confidence ≥ 0.01)
# ---------------------------------------------------------------------------


def test_sell_reachable_at_2_of_9():
    """2/9 bearish at 0.45 with real weights → SELL."""
    signals = _nth_bearish(2, 0.45)
    score = compute_global_weighted_score(signals)
    assert score["composite"] <= 0.49, (
        f"2/9 bearish at 0.45: composite={score['composite']:.3f} should be <= 0.49"
    )
    assert score["confidence"] >= 0.01, (
        f"2/9 bearish at 0.45: confidence={score['confidence']:.3f} should be >= 0.01"
    )


def test_sell_still_triggered_at_1_of_9():
    """1/9 bearish at 0.44 → still passes permissive threshold."""
    signals = _nth_bearish(1, 0.44)
    score = compute_global_weighted_score(signals)
    assert score["composite"] <= 0.49, (
        f"1/9 bearish at 0.44: composite={score['composite']:.3f} should be <= 0.49"
    )


# ---------------------------------------------------------------------------
# Documentation — formula trace (neutral thresholds)
# ---------------------------------------------------------------------------


def test_documentation_table(capsys):
    """Trace the formula at key (composite, consensus) points."""

    def _confidence(composite: float, bullish_n: int) -> float:
        magnitude = abs(composite - 0.5) * 2.0
        max_side = max(bullish_n, 9 - bullish_n)
        consensus_ratio = max_side / 9
        multiplier = min(1.0, 0.5 + consensus_ratio * 0.5)
        return magnitude * multiplier

    cases: list[tuple[str, float, int, float, bool]] = [
        ("2/9 at 0.55", 0.55, 2, _confidence(0.55, 2), _confidence(0.55, 2) >= 0.01),
        ("5/9 at 0.55", 0.55, 5, _confidence(0.55, 5), _confidence(0.55, 5) >= 0.01),
        ("7/9 at 0.55", 0.55, 7, _confidence(0.55, 7), _confidence(0.55, 7) >= 0.01),
        ("2/9 bear at 0.45", 0.45, 7, _confidence(0.45, 7), _confidence(0.45, 7) >= 0.01),
        ("5/9 bear at 0.45", 0.45, 4, _confidence(0.45, 4), _confidence(0.45, 4) >= 0.01),
    ]
    for label, _comp, _n, conf, triggers in cases:
        assert conf > 0, f"{label}: confidence should be > 0"
        flag = "TRIGGER" if triggers else "HOLD"
        print(f"  {label:20s} → confidence={conf:.3f} → {flag}")
