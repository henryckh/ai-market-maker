"""Batch signal adapter for the PerpEngine — batch desk execution + hedge fund pipeline.

Architecture:
  1. Run all agent desks on all symbols in parallel (batch, cross-sectional)
  2. Pipe desk outputs through the hedge fund pipeline:
     desk_debate → weighted_arbitrator → arbitrator_llm → risk_guard → BUY/SELL/HOLD
  3. Convert discrete BUY/SELL/HOLD to float target weight for the perp engine

The batch is correct — running all desks on all symbols at once per bar is efficient.
The hedge fund pipeline preserves the agentic theme: debate, arbitrator, Risk Guard.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def batch_signal_factory(
    bars_by_symbol: dict[str, list[list[float]]],
    symbols: list[str],
    *,
    config: dict[str, Any] | None = None,
    nexus_context_provider: Any | None = None,
    receipt_writer: Any | None = None,
    run_id: str = "",
    run_mem: Any | None = None,
) -> Any:
    c = config or {}

    from quant.agentic_arbitrator import run_all_desks_batch
    from quant.attribution import AttributionTracker

    # ---- Resolve deploy schema (accept resolved OR raw deploy shapes) ----
    deploy_config: dict[str, Any] = {}
    if isinstance(c.get("deploy_config"), dict):
        deploy_config = c["deploy_config"]
    elif isinstance(c.get("deploy"), dict):
        deploy_config = c["deploy"]

    def _parse_weights(src: dict[str, Any] | None) -> dict[str, float]:
        out: dict[str, float] = {}
        if not isinstance(src, dict):
            return out
        for k, v in src.items():
            try:
                w = float(v)
            except (TypeError, ValueError):
                continue
            if w > 0:
                out[str(k)] = w
        return out

    def _weights_from_agents(agents: dict[str, Any] | None) -> dict[str, float]:
        out: dict[str, float] = {}
        if not isinstance(agents, dict):
            return out
        for agent_id, meta in agents.items():
            if not isinstance(meta, dict) or meta.get("enabled", True) is False:
                continue
            try:
                w = float(meta.get("weight") or 0)
            except (TypeError, ValueError):
                continue
            if w > 0:
                out[str(agent_id)] = w
        return out

    # Prefer top-level resolved weights (loop/engine), then nested, then agents.
    profile_weights = _parse_weights(c.get("deploy_profile_weights"))
    if not profile_weights:
        profile_weights = _parse_weights(deploy_config.get("profile_weights"))
    if not profile_weights:
        profile_weights = _weights_from_agents(deploy_config.get("agents"))

    total = sum(profile_weights.values())
    if total > 0:
        profile_weights = {k: v / total for k, v in profile_weights.items()}
    else:
        raise ValueError(
            "agentic_batch: empty profile_weights — deploy config missing agents/"
            "profile_weights (silent HOLD path). Pass resolved deploy with agents "
            "or profile_weights."
        )

    # Decision threshold
    from backtest.agentic_defaults import default_agentic_decision_threshold

    decision_threshold: dict[str, Any] = default_agentic_decision_threshold()
    if isinstance(c.get("decision_threshold"), dict) and c["decision_threshold"]:
        decision_threshold = c["decision_threshold"]
    elif deploy_config:
        dt = deploy_config.get("decision_threshold")
        if isinstance(dt, dict) and dt:
            decision_threshold = dt

    # Enabled desks + LLM flags from agents block (must survive resolve_backtest_config)
    enabled_desk_ids: set[str] | None = None
    deployed_agents: dict[str, dict[str, Any]] = {}
    agents_block = (
        deploy_config.get("agents") if isinstance(deploy_config.get("agents"), dict) else {}
    )
    deployed_agents = {
        str(aid): meta for aid, meta in agents_block.items() if isinstance(meta, dict)
    }
    enabled = {
        str(aid) for aid, meta in deployed_agents.items() if meta.get("enabled", True) is not False
    }
    if enabled:
        enabled_desk_ids = enabled
    elif profile_weights:
        # Resolved config without agents: enable desks that have positive weight
        enabled_desk_ids = set(profile_weights.keys())

    # Arbitrator mode
    arbitrator_mode = (
        str(c.get("deploy_arbitrator_mode") or c.get("arbitrator_mode") or "").strip().lower()
    )
    exec_block = (
        deploy_config.get("execution") if isinstance(deploy_config.get("execution"), dict) else {}
    )
    if not arbitrator_mode and deploy_config:
        if exec_block.get("use_llm_synthesis") or exec_block.get("arbitrator_llm"):
            arbitrator_mode = "agent_llm"
        else:
            arbitrator_mode = "weighted_convergence"
    if not arbitrator_mode:
        arbitrator_mode = "weighted_convergence"
    is_llm_mode = arbitrator_mode in ("agent_llm", "llm", "full_agentic")

    # LLM desk IDs — from agents.llm_enabled; fail loud in agent_llm mode if missing
    llm_desk_ids: set[str] = set()
    if is_llm_mode:
        for agent_id, meta in deployed_agents.items():
            if meta.get("llm_enabled") is True:
                llm_desk_ids.add(str(agent_id))
        if not llm_desk_ids and not deployed_agents and is_llm_mode:
            logger.warning(
                "agentic_batch: arbitrator_mode=%s but deploy.agents missing — "
                "LLM desks disabled (deterministic fallback). Preserve agents in resolved deploy.",
                arbitrator_mode,
            )
        if llm_desk_ids:
            logger.info(
                "agentic_batch: LLM desks=%s arbitrator=%s", sorted(llm_desk_ids), arbitrator_mode
            )

    # Execution parameters
    max_leverage = float(c.get("leverage") or exec_block.get("leverage") or 3.0)
    max_position = float(c.get("max_position") or exec_block.get("max_position") or 0.25)
    # Slippage lives on the perp engine; keep on config for engine_cfg consumers.
    if c.get("slippage_bps") is None and exec_block.get("slippage_bps") is not None:
        try:
            c["slippage_bps"] = float(exec_block["slippage_bps"])
        except (TypeError, ValueError):
            pass
    allow_short = bool(exec_block.get("allows_short", True))
    use_debate = bool(exec_block.get("desk_debate_llm", False))
    use_arbitrator_llm = bool(exec_block.get("arbitrator_llm", False))
    if is_llm_mode and not use_arbitrator_llm and exec_block.get("use_llm_synthesis"):
        # use_llm_synthesis implies arbitrator LLM when arbitrator_llm omitted
        use_arbitrator_llm = True

    # State
    attribution_tracker = AttributionTracker()
    _bar_cache: dict[tuple[int, str], float] = {}
    interval_sec = int(c.get("interval_sec", 300))
    min_warmup = int(c.get("min_warmup_bars", 20) or 0)

    def _signal_fn(symbol: str, window: list, positions: Any, account: Any) -> float:
        bar_index = len(window) if isinstance(window, list) else 0

        if min_warmup > 0 and bar_index < min_warmup:
            return 0.0

        cache_key = (bar_index, symbol)
        if cache_key in _bar_cache:
            return _bar_cache[cache_key]

        try:
            # Build market_data
            market_data: dict[str, dict[str, Any]] = {
                s: {
                    "status": "success",
                    "backtest": True,
                    "ohlcv": list(bars_by_symbol.get(s, []))[:bar_index],
                }
                for s in symbols
            }

            # Resolve nexus context
            nexus_context = _resolve_nexus_context(
                nexus_context_provider, bar_index, window, symbols, interval_sec
            )

            # 1. Run all desks on all symbols (batch, parallel)
            desk_ids_to_run = list(enabled_desk_ids) if enabled_desk_ids else None
            desk_scores = run_all_desks_batch(
                symbols=symbols,
                market_data=market_data,
                nexus_context=nexus_context,
                desk_ids=desk_ids_to_run,
                llm_desk_ids=llm_desk_ids if llm_desk_ids else None,
                bars_by_symbol=bars_by_symbol,
                bar_index=bar_index,
                interval_sec=interval_sec,
            )

            # 2. Run desk debate (deterministic + optional LLM) for the primary symbol
            debate_entries: list[dict[str, Any]] = []
            if use_debate:
                try:
                    from workflow.desk_debate import (
                        deterministic_debate_entries,
                        llm_desk_debate_entries,
                    )

                    debate_state = _build_hedge_fund_state(
                        symbol=symbol,
                        bars_by_symbol=bars_by_symbol,
                        market_data=market_data,
                        nexus_context=nexus_context,
                        decision_threshold=decision_threshold,
                        profile_weights=profile_weights,
                        desk_scores=desk_scores,
                        bar_index=bar_index,
                        positions=positions,
                        account=account,
                    )
                    debate_entries = deterministic_debate_entries(debate_state)
                    debate_entries.extend(llm_desk_debate_entries(debate_state))
                except Exception as e:
                    logger.debug("Desk debate skipped: %s", e)

            # 3. Weighted arbitrator + optional LLM arbitrator overlay
            proposed_action, stance, confidence = _run_arbitration(
                symbol=symbol,
                desk_scores=desk_scores,
                profile_weights=profile_weights,
                decision_threshold=decision_threshold,
                debate_entries=debate_entries,
                use_arbitrator_llm=use_arbitrator_llm,
                bars_by_symbol=bars_by_symbol,
                market_data=market_data,
                nexus_context=nexus_context,
                bar_index=bar_index,
                positions=positions,
                account=account,
            )

            # 4. Risk Guard veto
            if proposed_action != "HOLD":
                vetoed = _run_risk_guard(
                    action=proposed_action,
                    stance=stance,
                    confidence=confidence,
                    symbol=symbol,
                    positions=positions,
                    account=account,
                    market_data=market_data,
                    bars_by_symbol=bars_by_symbol,
                    bar_index=bar_index,
                    max_leverage=max_leverage,
                    decision_threshold=decision_threshold,
                )
                if vetoed:
                    proposed_action = "HOLD"
                    stance = "neutral"
                    confidence = 0.0

            # 5. Convert BUY/SELL/HOLD to float weight
            target_weight = _trade_to_weight(
                action=proposed_action,
                stance=stance,
                confidence=confidence,
                max_position=max_position,
                allow_short=allow_short,
            )

            _bar_cache[(bar_index, symbol)] = target_weight

            # 6. Attribution tracking
            bar_returns = _compute_bar_returns(bars_by_symbol, symbols, bar_index)
            composite = _compute_composite_for_symbol(desk_scores, symbol, profile_weights)
            attribution_tracker.record_bar(
                desk_scores=desk_scores,
                desk_weights=profile_weights,
                final_weights={symbol: target_weight},
                bar_returns=bar_returns,
                composite_scores={symbol: composite},
            )

            # 7. Audit receipt
            if receipt_writer is not None:
                try:
                    import time as _time

                    receipt_writer.append(
                        {
                            "ts": _time.time(),
                            "run_id": run_id,
                            "bar_index": bar_index,
                            "strategy": "agentic_batch_hedge_fund",
                            "symbol": symbol,
                            "action": proposed_action,
                            "stance": stance,
                            "confidence": confidence,
                            "composite_score": composite,
                            "target_weight": target_weight,
                            "desk_scores": {
                                k: {s: v for s, v in sv.items() if abs(v - 0.5) > 0.1}
                                for k, sv in desk_scores.items()
                            },
                            "debate_entries": len(debate_entries),
                            "arbitrator_mode": arbitrator_mode,
                            "llm_arbitrator": use_arbitrator_llm,
                            "deploy_profile_weights": profile_weights,
                        }
                    )
                except Exception:
                    pass

        except Exception as exc:
            logger.error("Batch signal error at bar %d: %s", bar_index, exc)
            import traceback

            traceback.print_exc()
            _bar_cache[(bar_index, symbol)] = 0.0
            return 0.0

        return _bar_cache.get(cache_key, 0.0)

    _signal_fn.attribution = attribution_tracker
    _signal_fn.is_batch = True
    return _signal_fn


# ================================================================
# Hedge fund pipeline helpers
# ================================================================


def _compute_composite_for_symbol(
    desk_scores: dict[str, dict[str, float]],
    symbol: str,
    weights: dict[str, float],
) -> float:
    """Compute weighted composite score for one symbol from desk scores."""
    total = 0.0
    weighted_sum = 0.0
    for desk_id, sym_scores in desk_scores.items():
        w = weights.get(desk_id, 0.0)
        if w <= 0:
            continue
        score = sym_scores.get(symbol, 0.5)
        weighted_sum += w * score
        total += w
    if total > 0:
        return weighted_sum / total
    return 0.5


def _run_arbitration(
    symbol: str,
    desk_scores: dict[str, dict[str, float]],
    profile_weights: dict[str, float],
    decision_threshold: dict[str, Any],
    debate_entries: list[dict[str, Any]],
    use_arbitrator_llm: bool,
    bars_by_symbol: dict[str, list[list[float]]],
    market_data: dict[str, dict[str, Any]],
    nexus_context: dict[str, Any] | None,
    bar_index: int,
    positions: Any,
    account: Any,
) -> tuple[str, str, float]:
    """Run the weighted arbitrator with optional LLM overlay.

    Returns (action: BUY|SELL|HOLD, stance: bullish|bearish|neutral, confidence: 0-1).
    """
    # Compute composite from desk scores using profile weights
    composite = _compute_composite_for_symbol(desk_scores, symbol, profile_weights)

    # Determine stance from composite
    if composite >= 0.55:
        stance = "bullish"
    elif composite <= 0.45:
        stance = "bearish"
    else:
        stance = "neutral"

    # Confidence = how far from neutral
    confidence = min(0.95, abs(composite - 0.5) * 2.0)

    # Apply decision threshold gates
    buy_gate = decision_threshold.get("buy", {})
    sell_gate = decision_threshold.get("sell", {})
    buy_score = float(buy_gate.get("min_composite", 30)) / 100.0
    sell_score = float(sell_gate.get("max_composite", 70)) / 100.0
    buy_conf = float(buy_gate.get("min_confidence", 5)) / 100.0
    sell_conf = float(sell_gate.get("min_confidence", 5)) / 100.0

    action = "HOLD"
    if composite >= buy_score and confidence >= buy_conf:
        action = "BUY"
    elif composite <= sell_score and confidence >= sell_conf:
        action = "SELL"

    # Optional LLM arbitrator overlay
    if use_arbitrator_llm and action == "HOLD":
        try:
            from llm.arbitrator_llm import signal_arbitrator_llm

            state = _build_hedge_fund_state(
                symbol=symbol,
                bars_by_symbol=bars_by_symbol,
                market_data=market_data,
                nexus_context=nexus_context,
                decision_threshold=decision_threshold,
                profile_weights=profile_weights,
                desk_scores=desk_scores,
                bar_index=bar_index,
                positions=positions,
                account=account,
            )
            llm_result = signal_arbitrator_llm(state)
            intent = llm_result.get("trade_intent") or {}
            action = str(intent.get("action") or "HOLD").upper()
            stance = str(
                llm_result.get("proposed_signal", {}).get("params", {}).get("stance") or stance
            )
            confidence = float(
                llm_result.get("proposed_signal", {}).get("params", {}).get("confidence")
                or confidence
            )
        except Exception as e:
            logger.debug("LLM arbitrator overlay failed: %s", e)

    return action, stance, confidence


def _run_risk_guard(
    action: str,
    stance: str,
    confidence: float,
    symbol: str,
    positions: Any,
    account: Any,
    market_data: dict[str, dict[str, Any]],
    bars_by_symbol: dict[str, list[list[float]]],
    bar_index: int,
    max_leverage: float,
    decision_threshold: dict[str, Any],
) -> bool:
    """Run Risk Guard veto. Returns True if vetoed (block the trade)."""
    try:
        from backtest.engines.perp import coerce_account

        book = coerce_account(account) if account is not None else None
        if book is None:
            return False

        equity = getattr(book, "equity", 0) or 0
        pos_map = getattr(book, "positions", {}) or {}

        # Compute gross exposure
        gross = 0.0
        for sym_bars in bars_by_symbol.values():
            if bar_index > 0 and len(sym_bars) > bar_index:
                try:
                    gross += abs(float(sym_bars[bar_index][4]) * 0.01)
                except (IndexError, TypeError):
                    pass

        if equity > 1e-9 and gross > 0:
            lev = gross / equity
            if lev > max_leverage * 1.5:
                logger.warning(
                    "Risk Guard: leverage %s > %.1f, vetoing %s", lev, max_leverage, action
                )
                return True

        # Drawdown check: if equity < 50% of initial, block risk-increasing trades
        if equity < 5000 and action in ("BUY", "SELL"):
            if action == "BUY" and not _has_short(pos_map, symbol):
                return True
            if action == "SELL" and not _has_long(pos_map, symbol):
                return True

        return False
    except Exception as e:
        logger.debug("Risk Guard error: %s", e)
        return False


def _has_short(pos_map: dict, symbol: str) -> bool:
    try:
        qty = float(pos_map.get(symbol, {}).get("size", 0) or 0)
        return qty < -1e-12
    except (TypeError, ValueError, AttributeError):
        return False


def _has_long(pos_map: dict, symbol: str) -> bool:
    try:
        qty = float(pos_map.get(symbol, {}).get("size", 0) or 0)
        return qty > 1e-12
    except (TypeError, ValueError, AttributeError):
        return False


def _trade_to_weight(
    action: str,
    stance: str,
    confidence: float,
    max_position: float,
    allow_short: bool,
) -> float:
    """Convert BUY/SELL/HOLD to float target weight for the perp engine.

    Weight scale:
      +1.0 = full long (max_position)
      -1.0 = full short (-max_position)
       0.0 = flat (HOLD)
    """
    if action == "HOLD":
        return 0.0

    scale = max(0.25, min(1.0, confidence)) * max_position

    if action == "BUY":
        return scale
    elif action == "SELL" and allow_short:
        return -scale
    return 0.0


def _build_hedge_fund_state(
    symbol: str,
    bars_by_symbol: dict[str, list[list[float]]],
    market_data: dict[str, dict[str, Any]],
    nexus_context: dict[str, Any] | None,
    decision_threshold: dict[str, Any],
    profile_weights: dict[str, float],
    desk_scores: dict[str, dict[str, float]],
    bar_index: int,
    positions: Any,
    account: Any,
) -> dict[str, Any]:
    """Build a minimal HedgeFundState dict for the debate/arbitrator."""
    from backtest.engines.perp import coerce_account

    book = coerce_account(account) if account is not None else None

    state: dict[str, Any] = {
        "ticker": symbol,
        "universe": list(bars_by_symbol.keys()),
        "market_data": market_data,
        "decision_threshold": decision_threshold,
        "profile_weights": profile_weights,
        "shared_memory": {
            "nexus": nexus_context or {},
            "backtest": {
                "cash": book.cash if book else 0,
                "equity": book.equity if book else 0,
                "positions": getattr(book, "positions", {}) if book else {},
            },
        },
        "arbitrator_mode": "weighted_convergence",
    }

    # Build tier0_contracts from desk scores
    contracts = []
    for desk_id, sym_scores in desk_scores.items():
        score = sym_scores.get(symbol, 0.5)
        contract = {
            "agent_id": desk_id,
            "agent": desk_id,
            "source": "batch_engine",
            "status": "success",
            "composite": round(score * 100, 1),
            "confidence": abs(score - 0.5) * 2.0,
        }
        contracts.append(contract)
    state["tier0_contracts"] = contracts

    return state


# ================================================================
# Utility helpers
# ================================================================


def _resolve_nexus_context(
    nexus_provider: Any | None,
    bar_index: int,
    window: list,
    symbols: list[str],
    interval_sec: int,
) -> dict[str, Any] | None:
    if nexus_provider is None:
        try:
            from nexus_data.historical.provider import HistoricalNexusProvider

            nexus_provider = HistoricalNexusProvider()
        except ImportError:
            pass
    if nexus_provider is None:
        return None
    as_of_ms = None
    if isinstance(window, list) and window:
        try:
            ts = float(window[-1][0])
            if ts < 1e11:
                ts = ts * 1000
            as_of_ms = int(ts)
        except (IndexError, TypeError, ValueError):
            pass
    try:
        bundle = nexus_provider.get_bundle(
            as_of_ms=as_of_ms,
            universe=symbols,
            market_data={},
            primary=symbols[0] if symbols else "BTC/USDT",
        )
        return bundle
    except Exception as e:
        logger.warning("Nexus context resolution failed: %s", e)
        return None


def _compute_bar_returns(
    bars_by_symbol: dict[str, list[list[float]]],
    symbols: list[str],
    bar_index: int,
) -> dict[str, float]:
    returns: dict[str, float] = {}
    for sym in symbols:
        bars = bars_by_symbol.get(sym, [])
        if bar_index > 0 and len(bars) > bar_index:
            try:
                c_curr = float(bars[bar_index][4])
                c_prev = float(bars[bar_index - 1][4])
                returns[sym] = (c_curr - c_prev) / c_prev if c_prev > 0 else 0.0
            except (IndexError, TypeError, ValueError):
                returns[sym] = 0.0
        else:
            returns[sym] = 0.0
    return returns


__all__ = ["batch_signal_factory"]
