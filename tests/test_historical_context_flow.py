"""Pinned historical data actually reaches desks and LLM prompt context."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from agents.monetary_sentinel import MonetarySentinelAgent
from agents.news_narrative_miner import NewsNarrativeMinerAgent
from agents.statistical_alpha_engine import StatisticalAlphaEngineAgent
from agents.technical_ta_engine import TechnicalTaEngineAgent
from backtest.receipts import cot_snippets, nexus_asof_receipt
from llm.agent_llm_client import _build_market_context, _ohlcv_summary
from nexus_data.historical.provider import HistoricalNexusProvider

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


def _ms(day: str) -> int:
    dt = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _btc_bars(n: int = 80) -> list[list[float]]:
    csv = DATA / "ohlcv" / "BTC_USDT_1d.csv"
    if not csv.is_file():
        pytest.skip("pinned BTC csv missing")
    rows: list[list[float]] = []
    for line in csv.read_text(encoding="utf-8").splitlines()[1:]:
        parts = line.split(",")
        if len(parts) < 6:
            continue
        try:
            rows.append(
                [
                    float(parts[0]),
                    float(parts[1]),
                    float(parts[2]),
                    float(parts[3]),
                    float(parts[4]),
                    float(parts[5]),
                ]
            )
        except ValueError:
            continue
        if len(rows) >= n:
            break
    return rows


@pytest.mark.skipif(not (DATA / "fixtures" / "nexus_daily.jsonl").is_file(), reason="no fixtures")
def test_pinned_bundle_feeds_desks_and_llm_context() -> None:
    day = "2022-06-15"
    ticker = "BTC/USDT"
    bars = _btc_bars()
    assert bars and bars[0][5] > 0
    md = {ticker: {"ohlcv": bars}}
    bundle = HistoricalNexusProvider(root=DATA).get_bundle(
        as_of_ms=_ms(day),
        universe=[ticker],
        market_data=md,
        primary=ticker,
    )
    assert bundle.get("source") == "historical"
    assert bundle.get("as_of_date") == day
    eps = bundle.get("endpoints") or {}
    mo = (eps.get("market_overview") or {}).get("data") or {}
    assert mo.get("fear_greed_index") is not None
    news_items = ((eps.get("news") or {}).get("data") or {}).get("news") or []
    positions = (((eps.get("oi_top_ranking") or {}).get("data") or {}).get("data") or {}).get(
        "positions"
    ) or []
    assert news_items or positions, "expected news headlines or funding positions"

    news = NewsNarrativeMinerAgent().analyze(ticker=ticker, market_data=md, nexus_context=bundle)
    if news_items:
        assert news["status"] == "success"

    mon = MonetarySentinelAgent().analyze(ticker=ticker, market_data=md, nexus_context=bundle)
    assert mon["status"] == "success"
    assert mon["inputs"].get("fear_greed") is not None
    assert mon["liquidity_regime"] == "risk_off"

    alpha = StatisticalAlphaEngineAgent().analyze(
        ticker=ticker, market_data=md, nexus_context=bundle
    )
    if positions:
        assert alpha["status"] != "skipped"

    ta = TechnicalTaEngineAgent().analyze(ticker=ticker, market_data=md)
    assert ta.get("status") == "success"
    assert ta.get("ta_indicators")

    ctx = _build_market_context(
        {"ticker": ticker, "shared_memory": {"nexus": bundle}, "market_data": md},
        ticker=ticker,
    )
    assert "Fear & Greed" in ctx
    assert "Volume: last" in ctx
    assert "Funding rate" in ctx or "funding" in ctx.lower()
    if news_items:
        title = str(news_items[0].get("title") or "")
        if title:
            assert title[:40] in ctx or "Nexus Data" in ctx

    asof = nexus_asof_receipt(
        {
            "ticker": ticker,
            "market_data": md,
            "shared_memory": {"nexus": bundle},
        }
    )
    assert asof.get("as_of_date") == day
    assert asof.get("fear_greed") is not None
    assert asof.get("last_volume") is not None
    if news_items:
        assert asof.get("news_n", 0) >= 1
    if positions and positions[0].get("funding_rate") is not None:
        assert asof.get("funding_rate") is not None


def test_ohlcv_summary_includes_last_vs_avg_volume() -> None:
    bars = [[i, 1, 2, 0.5, 1.5, 100.0 + i] for i in range(10)]
    text = _ohlcv_summary(bars)
    assert "Volume: last" in text
    assert "vs avg" in text


def test_cot_snippets_keep_desk_reasoning() -> None:
    rows = cot_snippets(
        {
            "tier0_contracts": [
                {
                    "agent": "technical_ta_engine",
                    "source": "agent_llm",
                    "reasoning": "F&G 12 extreme fear; volume 1.8x avg; fade the bounce.",
                }
            ],
            "trade_intent": {"reasons": ["TA-led sell", "macro risk_off"]},
        }
    )
    agents = {r["agent_id"] for r in rows}
    assert "technical_ta_engine" in agents
    assert "signal_arbitrator" in agents
    assert any("F&G" in r["thought"] for r in rows)
