from __future__ import annotations

from config.agent_prompts import AgentPromptSettings
from llm.agent_llm_client import _build_market_context, _build_nexus_context
from llm.arbitrator_llm import merge_operator_arbitrator_prompt


def test_nexus_context_reads_historical_endpoints() -> None:
    state = {
        "shared_memory": {
            "nexus": {
                "as_of_date": "2022-06-15",
                "source": "historical",
                "endpoints": {
                    "news": {
                        "ok": True,
                        "data": {
                            "news": [
                                {
                                    "title": "SEC charges Celsius",
                                    "sentiment": -0.6,
                                }
                            ]
                        },
                    },
                    "market_overview": {
                        "ok": True,
                        "data": {
                            "fear_greed_index": 7,
                            "fear_greed_label": "Extreme Fear",
                            "vix": 33.2,
                            "effective_fed_funds_pct": 1.58,
                            "trade_weighted_usd_index": 119.4,
                            "stablecoin_change_7d_pct": -9.1,
                            "all_chain_tvl_change_7d_pct": -11.0,
                        },
                    },
                    "oi_top_ranking": {
                        "ok": True,
                        "data": {"data": {"positions": [{"funding_rate": 0.0001}]}},
                    },
                },
            }
        }
    }
    text = _build_nexus_context(state)
    assert "2022-06-15" in text
    assert "Fear & Greed: 7" in text
    assert "VIX 33.2" in text
    assert "fed funds 1.58%" in text
    assert "stablecoin 7d -9.1%" in text
    assert "SEC charges Celsius" in text
    assert "0.0001" in text


def test_nexus_context_still_reads_legacy_top_level_news() -> None:
    state = {
        "shared_memory": {
            "nexus": {
                "news": [{"title": "legacy headline", "sentiment": 0.2}],
                "funding": 0.01,
            }
        }
    }
    text = _build_nexus_context(state)
    assert "legacy headline" in text
    assert "0.01" in text


def test_market_context_includes_historical_nexus() -> None:
    state = {
        "ticker": "BTC/USDT",
        "shared_memory": {
            "nexus": {
                "as_of_date": "2022-06-15",
                "endpoints": {
                    "market_overview": {
                        "ok": True,
                        "data": {"fear_greed_index": 12, "fear_greed_label": "Fear"},
                    }
                },
            }
        },
    }
    ctx = _build_market_context(state, ticker="BTC/USDT")
    assert "Fear & Greed: 12" in ctx


def test_operator_prompt_appends_instead_of_replacing() -> None:
    engineered = "Keep churn_guard= in reasons."
    ps = AgentPromptSettings(
        node_id="n13",
        actor_id="signal_arbitrator",
        system_prompt="You are an ACTIVE trader.",
        task_prompt="Return JSON stance/confidence/reasons.",
        cot_enabled=True,
    )
    merged = merge_operator_arbitrator_prompt(engineered, ps)
    assert merged.startswith("Keep churn_guard=")
    assert "Operator policy:" in merged
    assert "ACTIVE trader" in merged
    assert "Operator task prompt:" in merged
    assert merged.index("churn_guard") < merged.index("ACTIVE trader")
