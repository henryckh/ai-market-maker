"""Deterministic unit tests for the quant package.

Tests: desk score extraction → attribution.
All tests are pure — no LLM, no I/O, no random seeds needed beyond what's built in.
"""

from __future__ import annotations

import math

# ================================================================
# agentic_arbitrator — Desk Score Extraction
# ================================================================


class TestExtractDeskScore:
    def test_neutral_on_error_status(self):
        from quant.agentic_arbitrator import _extract_desk_score

        score = _extract_desk_score("any_desk", {"status": "error"})
        assert score == 0.5

    def test_neutral_on_none_input(self):
        from quant.agentic_arbitrator import _extract_desk_score

        score = _extract_desk_score("unknown_desk", {})
        assert score == 0.5

    def test_ta_rsi_oversold_bullish(self):
        from quant.agentic_arbitrator import _extract_desk_score

        score = _extract_desk_score(
            "technical_ta_engine",
            {"ta_indicators": {"rsi": 20}},
        )
        assert score > 0.5

    def test_ta_rsi_overbought_bearish(self):
        from quant.agentic_arbitrator import _extract_desk_score

        score = _extract_desk_score(
            "technical_ta_engine",
            {"ta_indicators": {"rsi": 80}},
        )
        assert score < 0.5

    def test_ta_macd_tanh_normalization(self):
        from quant.agentic_arbitrator import _extract_desk_score

        score_strong = _extract_desk_score(
            "technical_ta_engine",
            {"ta_indicators": {"macd_hist": 0.5}},
        )
        score_weak = _extract_desk_score(
            "technical_ta_engine",
            {"ta_indicators": {"macd_hist": 0.0001}},
        )
        assert score_strong > 0.5
        assert abs(score_weak - 0.5) < abs(score_strong - 0.5)

    def test_monetary_sentinel_scoring(self):
        from quant.agentic_arbitrator import _extract_desk_score

        score = _extract_desk_score("monetary_sentinel", {"systemic_beta_score": 70})
        assert score > 0.5

    def test_pattern_recognition_scoring(self):
        from quant.agentic_arbitrator import _extract_desk_score

        score = _extract_desk_score("pattern_recognition_bot", {"setup_confidence_score": 80})
        assert score > 0.5

    def test_news_narrative_inverted(self):
        from quant.agentic_arbitrator import _extract_desk_score

        score = _extract_desk_score("news_narrative_miner", {"breaker_score": 80})
        assert score < 0.5


# ================================================================
# attribution — AttributionTracker
# ================================================================


class TestAttributionTracker:
    def test_record_bar_tracks_pnl(self):
        from quant.attribution import AttributionTracker

        tracker = AttributionTracker()
        tracker.record_bar(
            desk_scores={"desk_a": {"BTC": 0.8}},
            desk_weights={"desk_a": 1.0},
            final_weights={"BTC": 0.2},
            bar_returns={"BTC": 0.01},
        )
        summary = tracker.summary()
        assert summary["bar_count"] == 1
        assert summary["total_pnl"] > 0

    def test_nan_input_is_sanitized(self):
        from quant.attribution import AttributionTracker

        tracker = AttributionTracker()
        tracker.record_bar(
            desk_scores={"desk_a": {"BTC": 0.8}},
            desk_weights={"desk_a": 1.0},
            final_weights={"BTC": 0.2},
            bar_returns={"BTC": float("nan")},
        )
        summary = tracker.summary()
        assert summary["bar_count"] == 1
        assert summary["total_pnl"] == 0.0

    def test_inf_input_is_sanitized(self):
        from quant.attribution import AttributionTracker

        tracker = AttributionTracker()
        tracker.record_bar(
            desk_scores={"desk_a": {"BTC": 0.8}},
            desk_weights={"desk_a": 1.0},
            final_weights={"BTC": 0.2},
            bar_returns={"BTC": float("inf")},
        )
        summary = tracker.summary()
        assert not math.isnan(summary["total_pnl"])
        assert not math.isinf(summary["total_pnl"])

    def test_jsonl_no_nan(self):
        from quant.attribution import AttributionTracker

        tracker = AttributionTracker()
        tracker.record_bar(
            desk_scores={"desk_a": {"BTC": 0.8}},
            desk_weights={"desk_a": 1.0},
            final_weights={"BTC": 0.2},
            bar_returns={"BTC": float("nan")},
        )
        lines = tracker.to_jsonl()
        for line in lines:
            import json

            obj = json.loads(line)
            assert obj["total_pnl"] == 0.0

    def test_multiple_bars_cumulative(self):
        from quant.attribution import AttributionTracker

        tracker = AttributionTracker()
        for _ in range(5):
            tracker.record_bar(
                desk_scores={"desk_a": {"BTC": 0.8}},
                desk_weights={"desk_a": 1.0},
                final_weights={"BTC": 0.1},
                bar_returns={"BTC": 0.01},
            )
        summary = tracker.summary()
        assert summary["bar_count"] == 5
        assert summary["total_pnl"] > 0

    def test_multiple_desks(self):
        from quant.attribution import AttributionTracker

        tracker = AttributionTracker()
        tracker.record_bar(
            desk_scores={
                "desk_a": {"BTC": 0.8, "ETH": 0.4},
                "desk_b": {"BTC": 0.6, "ETH": 0.6},
            },
            desk_weights={"desk_a": 0.7, "desk_b": 0.3},
            final_weights={"BTC": 0.15, "ETH": -0.05},
            bar_returns={"BTC": 0.01, "ETH": -0.005},
        )
        summary = tracker.summary()
        assert len(summary["desk_cumulative_pnl"]) == 2
        assert "desk_a" in summary["desk_cumulative_pnl"]

    def test_zero_weight_skipped(self):
        from quant.attribution import AttributionTracker

        tracker = AttributionTracker()
        tracker.record_bar(
            desk_scores={"desk_a": {"BTC": 0.8}},
            desk_weights={"desk_a": 0.0},
            final_weights={"BTC": 0.2},
            bar_returns={"BTC": 0.01},
        )
        summary = tracker.summary()
        assert summary["desk_cumulative_pnl"].get("desk_a", 0.0) == 0.0

    def test_empty_reports(self):
        from quant.attribution import AttributionTracker

        tracker = AttributionTracker()
        summary = tracker.summary()
        assert summary["bar_count"] == 0
        assert summary["total_pnl"] == 0.0
