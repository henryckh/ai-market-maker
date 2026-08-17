"""Historical Nexus provider — same bundle contract as live, as-of safe."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from nexus_data.historical.store import (
    funding_as_of,
    load_defillama,
    load_fear_greed,
    load_fixture_for_date,
    load_fred,
    ms_to_utc_date,
)
from nexus_data.symbols import ccxt_to_nexus_pair_id

logger = logging.getLogger(__name__)


def _ok(data: Any) -> dict[str, Any]:
    return {"ok": True, "data": data}


def _empty() -> dict[str, Any]:
    return {"ok": False, "error": "unavailable_offline", "data": None}


def _as_by_symbol(ps: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(ps, dict):
        return {}
    inner = ps.get("by_symbol")
    if isinstance(inner, dict):
        return {k: v for k, v in inner.items() if isinstance(v, dict)}
    return {k: v for k, v in ps.items() if isinstance(v, dict) and k not in ("by_symbol", "errors")}


def _merge_per_symbol(
    fixture_ps: dict[str, Any] | None,
    derived_ps: dict[str, Any] | None,
) -> dict[str, Any]:
    """Keep OHLCV-derived TA blocks and overlay fixture news/sentiment per coin."""
    derived_by = _as_by_symbol(derived_ps)
    fixture_by = _as_by_symbol(fixture_ps)
    out: dict[str, Any] = {}
    for sym in set(derived_by) | set(fixture_by):
        # Derived TA last so fixture news/sentiment cannot clobber OHLCV indicators.
        out[sym] = {**(fixture_by.get(sym) or {}), **(derived_by.get(sym) or {})}
    return {"by_symbol": out, "errors": []}


def _normalize_news_contract(endpoints: dict[str, Any]) -> None:
    """Map fixture aggregates onto the field names news_narrative_miner already reads."""
    news = endpoints.get("news")
    if not isinstance(news, dict) or not news.get("ok"):
        return
    data = news.get("data")
    if not isinstance(data, dict):
        return
    agg = data.get("aggregate") if isinstance(data.get("aggregate"), dict) else {}
    impact = agg.get("impact_score")
    try:
        impact_f = float(impact) if impact is not None else 0.0
    except (TypeError, ValueError):
        impact_f = 0.0
    items = data.get("news")
    if impact_f > 0 and isinstance(items, list):
        stamped: list[Any] = []
        for row in items:
            if isinstance(row, dict) and row.get("impact_score") is None:
                stamped.append({**row, "impact_score": impact_f})
            else:
                stamped.append(row)
        data["news"] = stamped
        news["data"] = data
        endpoints["news"] = news

    if impact_f <= 0:
        return
    nas = endpoints.get("news_analytics_sentiment")
    nas_data: dict[str, Any] = {}
    if isinstance(nas, dict) and nas.get("ok") and isinstance(nas.get("data"), dict):
        nas_data = dict(nas["data"])
    nas_data.setdefault("News_Impact_Score", impact_f)
    nas_data.setdefault("news_impact_score", impact_f)
    nas_data.setdefault("impact_score", impact_f)
    endpoints["news_analytics_sentiment"] = _ok(nas_data)


def _overview_data(endpoints: dict[str, Any]) -> dict[str, Any]:
    mo = endpoints.get("market_overview")
    if isinstance(mo, dict) and mo.get("ok") and isinstance(mo.get("data"), dict):
        data = dict(mo["data"])
        endpoints["market_overview"] = {**mo, "data": data}
        return data
    data: dict[str, Any] = {"success": True}
    endpoints["market_overview"] = _ok(data)
    return data


def _overlay_fear_greed(endpoints: dict[str, Any], fng: dict[str, Any]) -> None:
    """Stamp F&G onto market_overview so monetary_sentinel can blend it."""
    data = _overview_data(endpoints)
    data["fear_greed_index"] = int(fng["value"])
    data["fear_greed_label"] = fng.get("label") or ""


def _overlay_fred(endpoints: dict[str, Any], fred: dict[str, Any]) -> None:
    data = _overview_data(endpoints)
    for key in (
        "vix",
        "us_10y_yield_pct",
        "effective_fed_funds_pct",
        "trade_weighted_usd_index",
        "sp500_index",
        "wti_crude_usd_per_bbl",
    ):
        if fred.get(key) is not None:
            data[key] = fred[key]
    data["fred_source"] = fred.get("source") or "fred_public_csv"


def _overlay_defillama(endpoints: dict[str, Any], llama: dict[str, Any]) -> None:
    data = _overview_data(endpoints)
    for key in (
        "stablecoin_circulating_usd",
        "stablecoin_change_7d_pct",
        "all_chain_tvl_usd",
        "all_chain_tvl_change_7d_pct",
    ):
        if llama.get(key) is not None:
            data[key] = llama[key]
    data["defillama_source"] = llama.get("source") or "defillama_public_aggregate_lag1"
    chg = llama.get("stablecoin_change_7d_pct")
    tvl_chg = llama.get("all_chain_tvl_change_7d_pct")
    parts = [float(x) for x in (chg, tvl_chg) if isinstance(x, (int, float))]
    if parts:
        # Map 7d % change onto 0–100 liquidity. 0% → 50; ±5pp → 20/80.
        mean_chg = sum(parts) / len(parts)
        liq = max(5.0, min(95.0, 50.0 + mean_chg * 6.0))
        data["onchain_liquidity_score"] = round(liq, 2)


class HistoricalNexusProvider:
    """Build nexus bundles from pinned offline data only."""

    name = "historical"

    def __init__(self, *, root: Path | None = None) -> None:
        self._root = root

    def get_bundle(
        self,
        *,
        as_of_ms: int | None = None,
        universe: list[str] | None = None,
        market_data: dict[str, Any] | None = None,
        primary: str | None = None,
    ) -> dict[str, Any]:
        ts = int(as_of_ms) if as_of_ms is not None else int(time.time() * 1000)
        day = ms_to_utc_date(ts)
        universe = [s for s in (universe or []) if isinstance(s, str)]
        primary = primary or (universe[0] if universe else "BTC/USDT")
        root = self._root

        fixture = load_fixture_for_date(day, root=root) or {}
        endpoints: dict[str, Any] = dict(fixture.get("endpoints") or {})
        per_symbol_fix = dict((fixture.get("per_symbol") or {}))

        fng = load_fear_greed(day, root=root)
        if fng is not None:
            endpoints.setdefault(
                "sentiment",
                _ok(
                    {
                        "score": (fng["value"] - 50) / 50.0,
                        "fear_greed": fng["value"],
                        "fear_greed_label": fng.get("label") or "",
                        "source": "offline_fear_greed",
                    }
                ),
            )

        derived_ps: dict[str, Any] | None = None
        if market_data:
            try:
                from backtest.ohlcv_derived_context import build_ohlcv_derived_nexus_context

                derived = build_ohlcv_derived_nexus_context(
                    ticker=str(primary),
                    universe=list(universe),
                    market_data=market_data,
                )
                for k, v in (derived.get("endpoints") or {}).items():
                    endpoints.setdefault(k, v)
                d_per = derived.get("per_symbol")
                if isinstance(d_per, dict) and d_per:
                    derived_ps = d_per
            except Exception as e:
                logger.debug("ohlcv derived context skipped: %s", e)

        if fng is not None:
            _overlay_fear_greed(endpoints, fng)
        fred = load_fred(day, root=root)
        if fred is not None:
            _overlay_fred(endpoints, fred)
        llama = load_defillama(day, root=root)
        if llama is not None:
            _overlay_defillama(endpoints, llama)

        fixture_by = _as_by_symbol(per_symbol_fix)
        primary_row = fixture_by.get(primary)
        if isinstance(primary_row, dict):
            if isinstance(primary_row.get("news"), dict):
                endpoints["news"] = primary_row["news"]
            if isinstance(primary_row.get("sentiment"), dict):
                endpoints.setdefault("news_analytics_sentiment", primary_row["sentiment"])

        positions = []
        for i, sym in enumerate(universe or [primary]):
            rate = funding_as_of(sym, ts, root=root)
            nid = ccxt_to_nexus_pair_id(sym) or sym.replace("/", "")
            row: dict[str, Any] = {
                "symbol": nid,
                "rank": i + 1,
                "score": 50.0,
            }
            if rate is not None:
                z = max(-3.0, min(3.0, float(rate) * 10000.0))
                row["funding_rate"] = rate
                row["funding_rate_z"] = z
                row["funding_premium_z"] = z
                row["score"] = 50.0 + z * 5.0
            positions.append(row)

        if positions:
            endpoints["oi_top_ranking"] = _ok({"data": {"positions": positions}})

        endpoints.setdefault("news", _empty())
        endpoints.setdefault("news_analytics_sentiment", endpoints.get("sentiment") or _empty())
        _normalize_news_contract(endpoints)

        errors: list[str] = []
        if not fixture:
            errors.append(f"no_daily_fixture:{day}")
        if not any(funding_as_of(s, ts, root=root) is not None for s in (universe or [primary])):
            errors.append("no_funding_series")

        per_out = _merge_per_symbol(per_symbol_fix, derived_ps)

        return {
            "fetched_at_epoch": time.time(),
            "as_of_ms": ts,
            "as_of_date": day,
            "source": "historical",
            "integration_contract_version": "2026-04-04",
            "endpoints": endpoints,
            "per_symbol": per_out,
            "errors": errors,
        }


__all__ = ["HistoricalNexusProvider"]
