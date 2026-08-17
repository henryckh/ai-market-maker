from __future__ import annotations

from typing import Any, Dict

from nexus_data.payload_extract import as_dict, first_float, unwrap_data


def _ohlcv_len(market_data: Any, ticker: str) -> int:
    if not isinstance(market_data, dict):
        return 0
    blob = market_data.get(ticker)
    if not isinstance(blob, dict):
        return 0
    ohlcv = blob.get("ohlcv")
    return len(ohlcv) if isinstance(ohlcv, list) else 0


def _ep(nexus_context: dict[str, Any] | None, name: str) -> dict[str, Any]:
    if not isinstance(nexus_context, dict):
        return {}
    eps = nexus_context.get("endpoints") or {}
    block = eps.get(name)
    return block if isinstance(block, dict) else {}


def _map_score(value: float, x0: float, x1: float, y0: float, y1: float) -> float:
    if x1 == x0:
        return (y0 + y1) / 2.0
    t = (value - x0) / (x1 - x0)
    t = max(0.0, min(1.0, t))
    return y0 + t * (y1 - y0)


def _regime_from_score(score: float) -> str:
    if score >= 65:
        return "risk_on"
    if score <= 35:
        return "risk_off"
    return "neutral"


class MonetarySentinelAgent:
    """Tier-0 AIMM: macro liquidity / systemic beta sentinel."""

    name = "monetary_sentinel"
    role = "macro_economist"

    def analyze(
        self,
        *,
        ticker: str,
        market_data: Dict[str, Any],
        nexus_context: dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        n = _ohlcv_len(market_data, ticker)
        score = 50.0
        if n >= 30:
            score = 60.0
        regime = "neutral"
        mo = _ep(nexus_context, "market_overview")
        nexus_ok = bool(mo.get("ok"))
        raw = mo.get("data") if nexus_ok else None
        overview = as_dict(unwrap_data(raw) if isinstance(raw, dict) else raw)

        ohlcv_liq = first_float(overview, "systemic_liquidity_score", "liquidity_score")
        if ohlcv_liq > 0:
            score = ohlcv_liq
            if overview.get("risk_on") is True:
                regime = "risk_on"
            elif overview.get("risk_off") is True:
                regime = "risk_off"
            else:
                regime = _regime_from_score(score)
        elif overview.get("risk_on") is True:
            score = min(95.0, score + 15.0)
            regime = "risk_on"
        elif overview.get("risk_off") is True:
            score = max(15.0, score - 15.0)
            regime = "risk_off"
        elif isinstance(raw, dict) and raw.get("success") is not False and overview:
            score = min(95.0, score + 10.0)
            regime = _regime_from_score(score)

        sent = _ep(nexus_context, "sentiment")
        sent_data = as_dict(unwrap_data(sent.get("data"))) if sent.get("ok") else {}
        fng_v = first_float(overview, "fear_greed_index", "fearGreedIndex", "crypto_fear_greed")
        if fng_v <= 0:
            fng_v = first_float(sent_data, "fear_greed", "fear_greed_index")

        vix = first_float(overview, "vix")
        dxy = first_float(overview, "trade_weighted_usd_index")
        fed = first_float(overview, "effective_fed_funds_pct")
        ten_y = first_float(overview, "us_10y_yield_pct")
        llama_liq = first_float(overview, "onchain_liquidity_score")
        stable_chg = first_float(overview, "stablecoin_change_7d_pct")
        tvl_chg = first_float(overview, "all_chain_tvl_change_7d_pct")

        parts: list[tuple[float, float]] = []
        if ohlcv_liq > 0:
            parts.append((ohlcv_liq, 0.15))
        if fng_v > 0:
            parts.append((fng_v, 0.30 if (vix > 0 or llama_liq > 0) else 0.55))
        if vix > 0:
            parts.append((_map_score(vix, 12.0, 35.0, 80.0, 20.0), 0.25))
        if llama_liq > 0:
            parts.append((llama_liq, 0.20))
        elif stable_chg or tvl_chg:
            chgs = [x for x in (stable_chg, tvl_chg) if x]
            mean_chg = sum(chgs) / len(chgs)
            parts.append((max(5.0, min(95.0, 50.0 + mean_chg * 6.0)), 0.20))
        if dxy > 0:
            parts.append((_map_score(dxy, 100.0, 125.0, 70.0, 25.0), 0.10))
        if fed > 0:
            parts.append((_map_score(fed, 0.1, 5.5, 75.0, 25.0), 0.10))

        if parts:
            wsum = sum(w for _, w in parts)
            score = sum(v * w for v, w in parts) / wsum
            score = max(10.0, min(95.0, score))
            regime = _regime_from_score(score)

        # Extremes still dominate: crypto panic, vol spike, or on-chain drain.
        if fng_v > 0 and fng_v <= 25:
            regime = "risk_off"
        elif fng_v >= 75:
            regime = "risk_on"
        if vix >= 32:
            regime = "risk_off"
        if stable_chg <= -8.0 or tvl_chg <= -8.0:
            regime = "risk_off"

        return {
            "status": "success",
            "systemic_beta_score": score,
            "liquidity_regime": regime,
            "inputs": {
                "ohlcv_candles": n,
                "nexus_market_overview": nexus_ok,
                "fear_greed": fng_v or None,
                "vix": vix or None,
                "us_10y_yield_pct": ten_y or None,
                "effective_fed_funds_pct": fed or None,
                "trade_weighted_usd_index": dxy or None,
                "onchain_liquidity_score": llama_liq or None,
                "stablecoin_change_7d_pct": stable_chg or None,
                "all_chain_tvl_change_7d_pct": tvl_chg or None,
                "nexus_overview_keys_sample": list(overview.keys())[:16] if overview else [],
            },
        }
