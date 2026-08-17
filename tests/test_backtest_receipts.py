"""Receipt helpers stamp historical as-of data and desk CoT."""

from __future__ import annotations

from backtest.receipts import build_tier0_summary, nexus_asof_receipt


def test_nexus_asof_reads_workflow_output_not_pre_scan_state() -> None:
    output = {
        "ticker": "BTC/USDT",
        "market_data": {"BTC/USDT": {"ohlcv": [[1, 1, 1, 1, 1, 2500.0]]}},
        "shared_memory": {
            "nexus": {
                "source": "historical",
                "as_of_date": "2022-06-15",
                "endpoints": {
                    "market_overview": {
                        "ok": True,
                        "data": {"fear_greed_index": 12, "fear_greed_label": "Extreme Fear"},
                    },
                    "news": {
                        "ok": True,
                        "data": {"news": [{"title": "3AC unwind hits BTC"}]},
                    },
                    "oi_top_ranking": {
                        "ok": True,
                        "data": {"data": {"positions": [{"funding_rate": -0.0003}]}},
                    },
                },
            }
        },
    }
    pre = {"ticker": "BTC/USDT", "shared_memory": {"backtest": {}}}
    assert nexus_asof_receipt(pre) == {}
    asof = nexus_asof_receipt(output)
    assert asof["as_of_date"] == "2022-06-15"
    assert asof["fear_greed"] == 12
    assert asof["news_n"] == 1
    assert asof["funding_rate"] == -0.0003
    assert asof["last_volume"] == 2500.0


def test_tier0_summary_keeps_llm_reasoning() -> None:
    rows = build_tier0_summary(
        {
            "tier0_contracts": [
                {
                    "agent": "technical_ta_engine",
                    "source": "agent_llm",
                    "composite": 38,
                    "confidence": 0.4,
                    "reasoning": "RSI overbought; F&G 12; fade.",
                    "ta_indicators": {"rsi": 72},
                }
            ]
        }
    )
    assert rows[0]["agent_id"] == "technical_ta_engine"
    assert "F&G" in rows[0]["reasoning"]
    assert rows[0]["signal"]["ta_indicators"]["rsi"] == 72
