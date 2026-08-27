"""Batch desk execution — run all agent desks on all symbols in one pass.

Architecture:
  1. run_all_desks_batch — compute all agent desk scores for all symbols in one pass
  2. LLM desks run inference in parallel (ThreadPoolExecutor)
  3. Deterministic desks run per-symbol via analyze()

This is the efficient batch layer. Cross-sectional awareness comes from
seeing all symbols at once. The hedge fund pipeline (debate → arbitrator →
risk guard) is applied by batch_signal_adapter.py, not here.

Key design decision: the batch executes desks in parallel per bar (correct
and efficient). The hedge fund team pipeline preserves the agentic theme —
specialist desks, debate, weighted arbitrator, and Risk Guard veto.
"""

from __future__ import annotations

import logging
import math
from typing import Any

logger = logging.getLogger(__name__)

# Import agent desks lazily to avoid circular imports
_AGENT_DESKS: dict[str, Any] | None = None


def _get_agent_desks() -> dict[str, Any]:
    global _AGENT_DESKS
    if _AGENT_DESKS is not None:
        return _AGENT_DESKS

    from agents.liquidity_order_flow import LiquidityOrderFlowAgent
    from agents.monetary_sentinel import MonetarySentinelAgent
    from agents.news_narrative_miner import NewsNarrativeMinerAgent
    from agents.pattern_recognition_bot import PatternRecognitionBotAgent
    from agents.pro_bias_analyst import ProBiasAnalystAgent
    from agents.retail_hype_tracker import RetailHypeTrackerAgent
    from agents.statistical_alpha_engine import StatisticalAlphaEngineAgent
    from agents.technical_ta_engine import TechnicalTaEngineAgent
    from agents.whale_behavior_analyst import WhaleBehaviorAnalystAgent

    _AGENT_DESKS = {
        "technical_ta_engine": TechnicalTaEngineAgent(),
        "monetary_sentinel": MonetarySentinelAgent(),
        "news_narrative_miner": NewsNarrativeMinerAgent(),
        "pattern_recognition_bot": PatternRecognitionBotAgent(),
        "statistical_alpha_engine": StatisticalAlphaEngineAgent(),
        "retail_hype_tracker": RetailHypeTrackerAgent(),
        "pro_bias_analyst": ProBiasAnalystAgent(),
        "whale_behavior_analyst": WhaleBehaviorAnalystAgent(),
        "liquidity_order_flow": LiquidityOrderFlowAgent(),
    }
    return _AGENT_DESKS


# ================================================================
# Batch desk execution — run all desks on all symbols
# ================================================================


def run_all_desks_batch(
    symbols: list[str],
    market_data: dict[str, dict[str, Any]],
    nexus_context: dict[str, Any] | None = None,
    desk_ids: list[str] | None = None,
    *,
    llm_desk_ids: set[str] | None = None,
    bars_by_symbol: dict[str, list[list[float]]] | None = None,
    bar_index: int = 0,
    interval_sec: int = 3600,
) -> dict[str, dict[str, float]]:
    """Run all agent desks against ALL symbols in one batch.

    Each desk's analyze() is called per-symbol (preserving the existing desk API),
    but all symbols are processed in a single pass. The result is a cross-sectional
    signal matrix [N_desks × N_symbols].

    When ``llm_desk_ids`` is provided, those desks use LLM inference (infer_agent)
    instead of deterministic analyze(). LLM desks get rich OHLCV + Nexus context.

    Args:
        symbols: universe ["BTC/USDT", "ETH/USDT", ...]
        market_data: {symbol: {"ohlcv": [...], ...}, ...}
        nexus_context: optional nexus bundle
        desk_ids: optional subset (default: all 9)
        llm_desk_ids: set of desk IDs to use LLM inference for
        bars_by_symbol: OHLCV bars for LLM context {symbol: [[ts,o,h,l,c,v], ...]}
        bar_index: current bar index for LLM timestamp
        interval_sec: bar interval in seconds

    Returns:
        {desk_id: {symbol: score_0_1, ...}, ...}
    """
    desks = _get_agent_desks()
    desk_ids = desk_ids or list(desks.keys())
    desk_ids = [d for d in desk_ids if d in desks]
    llm_desks = set(llm_desk_ids) if llm_desk_ids else set()

    results: dict[str, dict[str, float]] = {}
    llm_desk_ids_to_run = [d for d in desk_ids if d in llm_desks]
    non_llm_desk_ids = [d for d in desk_ids if d not in llm_desks]

    # ---- LLM desks: parallel inference per symbol ----
    if llm_desk_ids_to_run:
        try:
            from concurrent.futures import ThreadPoolExecutor, as_completed

            from llm.agent_llm_client import infer_agent

            _executor = ThreadPoolExecutor(
                max_workers=min(8, len(symbols) * len(llm_desk_ids_to_run))
            )

            def _run_llm_desk_symbol(desk_id: str, sym: str) -> tuple[str, str, float]:
                """Run LLM inference for one desk on one symbol. Returns (desk_id, sym, score)."""
                try:
                    llm_state = _build_llm_inference_state(
                        ticker=sym,
                        bars_by_symbol=bars_by_symbol or {},
                        market_data=market_data,
                        nexus_context=nexus_context,
                        bar_index=bar_index,
                        interval_sec=interval_sec,
                    )
                    llm_output = infer_agent(agent_id=desk_id, state=llm_state, ticker=sym)
                    return (desk_id, sym, _extract_desk_score(desk_id, llm_output))
                except Exception as e:
                    logger.warning(
                        "Desk %s LLM inference failed for %s: %s (falling back to deterministic)",
                        desk_id,
                        sym,
                        e,
                    )
                    has_nexus = desk_id != "technical_ta_engine"
                    return (
                        desk_id,
                        sym,
                        _run_deterministic_desk(
                            desks[desk_id],
                            desk_id,
                            sym,
                            market_data,
                            nexus_context,
                            has_nexus,
                        ),
                    )

            futures = {}
            for desk_id in llm_desk_ids_to_run:
                results.setdefault(desk_id, {})
                for sym in symbols:
                    futures[_executor.submit(_run_llm_desk_symbol, desk_id, sym)] = (desk_id, sym)

            for future in as_completed(futures):
                desk_id, sym, score = future.result()
                results[desk_id][sym] = score

            _executor.shutdown(wait=True)
        except ImportError:
            logger.warning("concurrent.futures unavailable, falling back to serial LLM")
            for desk_id in llm_desk_ids_to_run:
                results.setdefault(desk_id, {})
                agent = desks[desk_id]
                has_nexus = desk_id != "technical_ta_engine"
                for sym in symbols:
                    try:
                        llm_state = _build_llm_inference_state(
                            ticker=sym,
                            bars_by_symbol=bars_by_symbol or {},
                            market_data=market_data,
                            nexus_context=nexus_context,
                            bar_index=bar_index,
                            interval_sec=interval_sec,
                        )
                        llm_output = infer_agent(agent_id=desk_id, state=llm_state, ticker=sym)
                        results[desk_id][sym] = _extract_desk_score(desk_id, llm_output)
                    except Exception as e:
                        logger.warning("Desk %s LLM failed for %s: %s", desk_id, sym, e)
                        results[desk_id][sym] = _run_deterministic_desk(
                            agent,
                            desk_id,
                            sym,
                            market_data,
                            nexus_context,
                            has_nexus,
                        )
        except Exception as e:
            logger.warning("LLM parallel inference setup failed: %s", e)
            non_llm_desk_ids = desk_ids

    # ---- Deterministic desks ----
    for desk_id in non_llm_desk_ids:
        agent = desks[desk_id]
        has_nexus = desk_id != "technical_ta_engine"
        symbol_scores: dict[str, float] = {}
        for sym in symbols:
            try:
                symbol_scores[sym] = _run_deterministic_desk(
                    agent,
                    desk_id,
                    sym,
                    market_data,
                    nexus_context,
                    has_nexus,
                )
            except Exception as e:
                logger.warning("Desk %s failed for %s: %s", desk_id, sym, e)
                symbol_scores[sym] = 0.5
        results[desk_id] = symbol_scores

    return results


def _run_deterministic_desk(
    agent: Any,
    desk_id: str,
    sym: str,
    market_data: dict[str, dict[str, Any]],
    nexus_context: dict[str, Any] | None,
    has_nexus: bool,
) -> float:
    """Run a single deterministic desk on a single symbol."""
    kwargs: dict[str, Any] = {"ticker": sym, "market_data": market_data}
    if has_nexus and nexus_context is not None:
        kwargs["nexus_context"] = nexus_context
    output = agent.analyze(**kwargs)
    return _extract_desk_score(desk_id, output)


def _build_llm_inference_state(
    *,
    ticker: str,
    bars_by_symbol: dict[str, list[list[float]]],
    market_data: dict[str, dict[str, Any]],
    nexus_context: dict[str, Any] | None,
    bar_index: int,
    interval_sec: int,
) -> dict[str, Any]:
    """Build a state dict for infer_agent() from batch backtest parameters."""
    state: dict[str, Any] = {
        "ticker": ticker,
        "bars_by_symbol": bars_by_symbol,
        "market_data": market_data,
        "shared_memory": {
            "nexus": nexus_context,
            "backtest": {},
        },
    }
    if bar_index > 0 and bars_by_symbol:
        sym_bars = bars_by_symbol.get(ticker, [])
        if bar_index < len(sym_bars):
            ts_ms = int(sym_bars[bar_index][0])
            if ts_ms < 1e11:
                ts_ms = ts_ms * 1000
            state["ts_ms"] = ts_ms
            state["shared_memory"]["backtest"]["window_last_ts_ms"] = ts_ms
    return state


# ================================================================
# Score extraction — map desk output → 0-1 scalar
# ================================================================


def _extract_desk_score(desk_id: str, output: dict[str, Any]) -> float:
    """Extract a normalized 0-1 scalar from a desk's analyze() output.

    >0.5 = bullish, <0.5 = bearish, 0.5 = neutral/error.
    Handles both deterministic (snake_case) and LLM (PascalCase) field names.
    """
    if not isinstance(output, dict) or output.get("status") == "error":
        return 0.5

    if desk_id == "monetary_sentinel":
        raw = float(output.get("systemic_beta_score") or output.get("Liquidity_Score", 50))
        return min(1.0, max(0.0, raw / 100.0))

    if desk_id == "news_narrative_miner":
        raw = float(output.get("breaker_score") or output.get("News_Impact_Score", 50))
        return min(1.0, max(0.0, 1.0 - raw / 100.0))

    if desk_id == "pattern_recognition_bot":
        raw = float(output.get("setup_confidence_score") or output.get("Setup_Score", 50))
        return min(1.0, max(0.0, raw / 100.0))

    if desk_id == "statistical_alpha_engine":
        signal = str(output.get("alpha_signal", "")).lower()
        raw_z = output.get("cross_sectional_z_score")
        z = float(raw_z) if raw_z is not None else 0.0
        if signal == "long_bias":
            return min(1.0, max(0.5, 0.5 + z / 3.0))
        elif signal == "short_bias":
            return max(0.0, min(0.5, 0.5 + z / 3.0))
        return 0.5

    if desk_id == "technical_ta_engine":
        ta = output.get("ta_indicators") or {}
        if not isinstance(ta, dict):
            return 0.5
        values: list[float] = []
        rsi = ta.get("rsi")
        if rsi is not None:
            values.append(1.0 - min(1.0, max(0.0, float(rsi) / 100.0)))
        macd_h = ta.get("macd_hist")
        if macd_h is not None:
            values.append(0.5 + 0.5 * math.tanh(float(macd_h) * 100.0))
        pm = ta.get("price_momentum")
        if pm is not None:
            values.append(min(1.0, max(0.0, float(pm) / 100.0)))
        if not values:
            return 0.5
        return sum(values) / len(values)

    if desk_id == "retail_hype_tracker":
        fomo = float(output.get("fomo_level") or output.get("FOMO_Level", 50))
        contra = 1.0 - min(1.0, max(0.0, fomo / 100.0))
        if output.get("divergence_warning") or output.get("Divergence_Warning"):
            contra = min(contra, 0.4)
        return contra

    if desk_id == "pro_bias_analyst":
        raw = float(output.get("pro_bias_score") or output.get("Pro_Bias", 50))
        return min(1.0, max(0.0, raw / 100.0))

    if desk_id == "whale_behavior_analyst":
        dump = float(output.get("dump_probability") or output.get("Dump_Probability", 50))
        pump = float(output.get("pump_probability") or output.get("Sell_Pressure_Gauge", 50))
        pump_norm = min(1.0, max(0.0, 1.0 - pump / 100.0))
        dump_norm = min(1.0, max(0.0, dump / 100.0))
        return min(1.0, max(0.0, (pump_norm + (1.0 - dump_norm)) / 2.0))

    if desk_id == "liquidity_order_flow":
        raw = float(output.get("slippage_risk_score") or output.get("Slippage_Risk_Score", 50))
        return min(1.0, max(0.0, 1.0 - raw / 100.0))

    return 0.5


__all__ = [
    "run_all_desks_batch",
]
