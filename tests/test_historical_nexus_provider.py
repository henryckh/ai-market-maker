"""Historical Nexus provider: as-of bundles, contract mapping, no look-ahead."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from agents.monetary_sentinel import MonetarySentinelAgent
from agents.news_narrative_miner import NewsNarrativeMinerAgent
from agents.statistical_alpha_engine import StatisticalAlphaEngineAgent
from nexus_data.historical.provider import HistoricalNexusProvider
from nexus_data.provider import resolve_nexus_provider


def _ms(day: str) -> int:
    dt = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _write_offline_root(tmp: Path, *, day: str = "2022-06-15") -> Path:
    (tmp / "fixtures").mkdir(parents=True)
    (tmp / "macro").mkdir()
    (tmp / "derivatives").mkdir()
    fixture = {
        "date": day,
        "source": "offline_fixtures",
        "endpoints": {
            "news": {
                "ok": True,
                "data": {
                    "news": [
                        {
                            "title": f"offline_news_BTC_{day}_0",
                            "sentiment": -0.4,
                            "source": "cryptovision_offline",
                        }
                    ],
                    "aggregate": {
                        "count": 12,
                        "mean_sentiment": -0.4,
                        "positive": 2,
                        "negative": 8,
                        "neutral": 2,
                        "impact_score": 44.0,
                    },
                },
            }
        },
        "per_symbol": {
            "ETH/USDT": {
                "news": {
                    "ok": True,
                    "data": {
                        "news": [
                            {
                                "title": f"offline_news_ETH_{day}_0",
                                "sentiment": 0.2,
                                "source": "cryptovision_offline",
                            }
                        ],
                        "aggregate": {
                            "count": 4,
                            "mean_sentiment": 0.2,
                            "impact_score": 28.0,
                        },
                    },
                }
            }
        },
    }
    (tmp / "fixtures" / "nexus_daily.jsonl").write_text(
        json.dumps(fixture) + "\n", encoding="utf-8"
    )
    (tmp / "macro" / "fear_greed_daily.csv").write_text(
        "date,value,label\n2022-06-15,12,Extreme Fear\n",
        encoding="utf-8",
    )
    (tmp / "macro" / "fred_daily.csv").write_text(
        "date,vix,us_10y_yield_pct,effective_fed_funds_pct,trade_weighted_usd_index,"
        "us_high_yield_oas_pct,sp500_index,wti_crude_usd_per_bbl,source\n"
        "2022-06-15,34.0,3.3,1.58,120.0,,3800,110.0,fred_public_csv\n",
        encoding="utf-8",
    )
    (tmp / "macro" / "defillama_liquidity_daily.csv").write_text(
        "date,stablecoin_circulating_usd,stablecoin_change_7d_pct,all_chain_tvl_usd,"
        "all_chain_tvl_change_7d_pct,source\n"
        "2022-06-15,150000000000,-9.5,80000000000,-12.0,defillama_public_aggregate_lag1\n",
        encoding="utf-8",
    )
    ts = _ms(day)
    (tmp / "derivatives" / "funding_all.csv").write_text(
        f"symbol,timestamp_ms,funding_rate\nBTCUSDT,{ts},0.0001\nETHUSDT,{ts},-0.0002\n",
        encoding="utf-8",
    )
    return tmp


def _bars(n: int = 40, *, start: float = 100.0, drift: float = -0.01) -> list[list[float]]:
    rows: list[list[float]] = []
    price = start
    for i in range(n):
        ts = 1_655_251_200_000 + i * 86_400_000
        price *= 1.0 + drift
        rows.append([ts, price, price, price, price, 1000.0])
    return rows


def test_resolve_provider_picks_historical_in_backtest():
    p = resolve_nexus_provider(run_mode="backtest")
    assert p.name == "historical"
    live = resolve_nexus_provider(run_mode="paper")
    assert live.name == "live"


def test_bundle_maps_news_impact_and_fear_greed(tmp_path: Path):
    root = _write_offline_root(tmp_path)
    provider = HistoricalNexusProvider(root=root)
    md = {"BTC/USDT": {"ohlcv": _bars()}}
    bundle = provider.get_bundle(
        as_of_ms=_ms("2022-06-15"),
        universe=["BTC/USDT", "ETH/USDT"],
        market_data=md,
        primary="BTC/USDT",
    )
    assert bundle["source"] == "historical"
    assert bundle["as_of_date"] == "2022-06-15"
    nas = bundle["endpoints"]["news_analytics_sentiment"]
    assert nas["ok"] is True
    assert float(nas["data"]["News_Impact_Score"]) == 44.0
    items = bundle["endpoints"]["news"]["data"]["news"]
    assert items[0]["impact_score"] == 44.0
    mo = bundle["endpoints"]["market_overview"]["data"]
    assert mo["fear_greed_index"] == 12
    assert mo["vix"] == 34.0
    assert mo["effective_fed_funds_pct"] == 1.58
    assert mo["stablecoin_change_7d_pct"] == -9.5
    assert mo["onchain_liquidity_score"] < 50
    assert "systemic_liquidity_score" in mo
    ta = bundle["per_symbol"]["by_symbol"]["BTC/USDT"]["technical_analysis"]
    assert ta["ok"] is True
    pos = bundle["endpoints"]["oi_top_ranking"]["data"]["data"]["positions"]
    assert pos[0]["funding_rate"] == 0.0001


def test_derived_ta_wins_over_fixture_ta(tmp_path: Path):
    root = _write_offline_root(tmp_path)
    fixture_path = root / "fixtures" / "nexus_daily.jsonl"
    row = json.loads(fixture_path.read_text(encoding="utf-8").splitlines()[0])
    row.setdefault("per_symbol", {})["BTC/USDT"] = {
        "technical_analysis": {"ok": True, "data": {"rsi": 1, "source": "fixture_junk"}},
        "news": row["endpoints"]["news"],
    }
    fixture_path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    provider = HistoricalNexusProvider(root=root)
    bundle = provider.get_bundle(
        as_of_ms=_ms("2022-06-15"),
        universe=["BTC/USDT"],
        market_data={"BTC/USDT": {"ohlcv": _bars()}},
        primary="BTC/USDT",
    )
    ta = bundle["per_symbol"]["by_symbol"]["BTC/USDT"]["technical_analysis"]
    payload = ta.get("data") if isinstance(ta, dict) else {}
    assert payload.get("source") != "fixture_junk"
    assert payload.get("rsi") != 1


def test_primary_symbol_news_preferred(tmp_path: Path):
    root = _write_offline_root(tmp_path)
    provider = HistoricalNexusProvider(root=root)
    bundle = provider.get_bundle(
        as_of_ms=_ms("2022-06-15"),
        universe=["ETH/USDT"],
        primary="ETH/USDT",
    )
    titles = [r["title"] for r in bundle["endpoints"]["news"]["data"]["news"]]
    assert any("ETH" in t for t in titles)


def test_funding_as_of_does_not_look_ahead(tmp_path: Path):
    root = _write_offline_root(tmp_path, day="2022-06-15")
    later = _ms("2022-06-15")
    earlier = later - 86_400_000
    (root / "derivatives" / "funding_all.csv").write_text(
        f"symbol,timestamp_ms,funding_rate\nBTCUSDT,{later},0.0099\n",
        encoding="utf-8",
    )
    provider = HistoricalNexusProvider(root=root)
    bundle = provider.get_bundle(
        as_of_ms=earlier,
        universe=["BTC/USDT"],
        primary="BTC/USDT",
    )
    pos = bundle["endpoints"]["oi_top_ranking"]["data"]["data"]["positions"][0]
    assert "funding_rate" not in pos
    assert "no_funding_series" in bundle["errors"]


def test_desks_consume_historical_bundle(tmp_path: Path):
    root = _write_offline_root(tmp_path)
    provider = HistoricalNexusProvider(root=root)
    ticker = "BTC/USDT"
    md = {ticker: {"ohlcv": _bars()}}
    bundle = provider.get_bundle(
        as_of_ms=_ms("2022-06-15"),
        universe=[ticker],
        market_data=md,
        primary=ticker,
    )
    news = NewsNarrativeMinerAgent().analyze(ticker=ticker, market_data=md, nexus_context=bundle)
    assert news["status"] == "success"
    assert news["breaker_score"] > 0
    mon = MonetarySentinelAgent().analyze(ticker=ticker, market_data=md, nexus_context=bundle)
    assert mon["status"] == "success"
    assert mon["liquidity_regime"] == "risk_off"
    assert mon["inputs"]["fear_greed"] == 12
    assert mon["inputs"]["vix"] == 34.0
    assert mon["inputs"]["stablecoin_change_7d_pct"] == -9.5
    alpha = StatisticalAlphaEngineAgent().analyze(
        ticker=ticker, market_data=md, nexus_context=bundle
    )
    assert alpha["status"] != "skipped"


def test_fred_and_defillama_do_not_look_ahead(tmp_path: Path) -> None:
    root = _write_offline_root(tmp_path, day="2022-06-15")
    provider = HistoricalNexusProvider(root=root)
    bundle = provider.get_bundle(
        as_of_ms=_ms("2022-06-14"),
        universe=["BTC/USDT"],
        primary="BTC/USDT",
    )
    mo = (bundle["endpoints"].get("market_overview") or {}).get("data") or {}
    assert mo.get("vix") is None
    assert mo.get("stablecoin_change_7d_pct") is None


def test_sentinel_fng_only_still_risk_off() -> None:
    mon = MonetarySentinelAgent().analyze(
        ticker="BTC/USDT",
        market_data={"BTC/USDT": {"ohlcv": _bars()}},
        nexus_context={
            "endpoints": {
                "market_overview": {
                    "ok": True,
                    "data": {"fear_greed_index": 12, "fear_greed_label": "Extreme Fear"},
                }
            }
        },
    )
    assert mon["liquidity_regime"] == "risk_off"
    assert mon["inputs"]["vix"] is None
