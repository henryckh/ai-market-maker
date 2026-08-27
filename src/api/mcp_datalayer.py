"""Read-only Datalayer proxies for Strategy API tools.

Same X-API-KEY as strategy tools. Does not wrap the full REST surface —
only the few reads that are useful at event/agent time.
"""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import urljoin

import httpx

DEFAULT_BASE = "https://api-data.olaxbt.xyz"
TIMEOUT_SEC = 6.0
MAX_NEWS = 10
MAX_SYMBOLS = 5
MAX_JSON_CHARS = 120_000


def datalayer_base_url() -> str:
    raw = (
        os.getenv("DATALAYER_API_URL") or os.getenv("DATALAYER_API_BASE_URL") or DEFAULT_BASE
    ).strip()
    return raw.rstrip("/")


def _compact(value: Any, *, depth: int = 0) -> Any:
    if isinstance(value, list):
        if len(value) > 8:
            tail = [_compact(x, depth=depth + 1) for x in value[-3:]]
            return {"_len": len(value), "tail": tail}
        return [_compact(x, depth=depth + 1) for x in value]
    if isinstance(value, dict):
        if depth > 3:
            return {k: "…" for k in list(value)[:12]}
        return {str(k): _compact(v, depth=depth + 1) for k, v in list(value.items())[:40]}
    if isinstance(value, str) and len(value) > 400:
        return value[:400] + "…"
    return value


def _trim(payload: Any) -> Any:
    compact = _compact(payload)
    raw = str(compact)
    if len(raw) <= MAX_JSON_CHARS:
        return compact
    return {"truncated": True, "note": "payload compacted for MCP", "preview": compact}


def datalayer_get(
    path: str,
    *,
    params: dict[str, str] | None = None,
    api_key: str = "",
) -> Any:
    url = urljoin(datalayer_base_url() + "/", path.lstrip("/"))
    headers = {"Accept": "application/json"}
    key = (api_key or "").strip() or (
        os.getenv("DATALAYER_API_KEY") or os.getenv("NEXUS_API_KEY") or ""
    ).strip()
    if key:
        headers["X-API-KEY"] = key
    try:
        with httpx.Client(timeout=TIMEOUT_SEC) as client:
            res = client.get(url, headers=headers, params=params or {})
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Datalayer unreachable: {exc}") from exc
    if res.status_code >= 400:
        detail = res.text[:400]
        raise RuntimeError(f"Datalayer HTTP {res.status_code}: {detail}")
    try:
        return res.json()
    except ValueError as exc:
        raise RuntimeError("Datalayer returned non-JSON") from exc


def _parse_symbols(raw: Any) -> list[str]:
    if isinstance(raw, list):
        parts = [str(x).strip() for x in raw]
    else:
        parts = [p.strip() for p in str(raw or "").split(",")]
    out = [p for p in parts if p]
    if not out:
        return ["BTC/USDT"]
    return out[:MAX_SYMBOLS]


def get_market_snapshot(args: dict[str, Any], *, api_key: str) -> dict[str, Any]:
    as_of = str(args.get("as_of") or args.get("date") or "").strip()
    if not as_of:
        raise ValueError("as_of is required (YYYY-MM-DD)")
    symbols = _parse_symbols(args.get("symbols") or args.get("universe") or args.get("symbol"))
    universe = ",".join(symbols)
    raw = datalayer_get(
        "/api/v1/historical/nexus",
        params={"as_of": as_of, "universe": universe},
        api_key=api_key,
    )
    data = raw.get("data") if isinstance(raw, dict) else None
    if not isinstance(data, dict):
        return _trim(raw)
    per = data.get("per_symbol") if isinstance(data.get("per_symbol"), dict) else {}
    by_symbol = per.get("by_symbol") or per.get("bySymbol") or {}
    if not isinstance(by_symbol, dict):
        by_symbol = {}
    return _trim(
        {
            "as_of_date": data.get("as_of_date"),
            "source": data.get("source"),
            "symbols": {k: by_symbol.get(k) for k in symbols if k in by_symbol} or by_symbol,
            "note": data.get("note"),
        }
    )


def get_fear_greed(args: dict[str, Any], *, api_key: str) -> dict[str, Any]:
    as_of = str(args.get("as_of") or args.get("date") or "").strip()
    params: dict[str, str] = {}
    if as_of:
        params["to"] = as_of
        params["from"] = as_of
    raw = datalayer_get("/api/v1/historical/fear-greed", params=params or None, api_key=api_key)
    data = raw.get("data") if isinstance(raw, dict) else raw
    return _trim(data if data is not None else raw)


def get_news(args: dict[str, Any], *, api_key: str) -> dict[str, Any]:
    try:
        limit = int(args.get("limit") or MAX_NEWS)
    except (TypeError, ValueError):
        limit = MAX_NEWS
    limit = max(1, min(MAX_NEWS, limit))
    params: dict[str, str] = {"limit": str(limit)}
    symbol = str(args.get("symbol") or args.get("crypto") or "").strip()
    if symbol:
        params["crypto"] = symbol.replace("/USDT", "").replace("USDT", "")
    hours = str(args.get("hours") or "").strip()
    if hours:
        params["hours"] = hours
    raw = datalayer_get("/api/news", params=params, api_key=api_key)
    items = []
    if isinstance(raw, dict):
        items = raw.get("news") or raw.get("data") or raw.get("items") or []
    if not isinstance(items, list):
        items = []
    slim = []
    for row in items[:limit]:
        if not isinstance(row, dict):
            continue
        slim.append(
            {
                "title": row.get("title") or row.get("headline"),
                "source": row.get("source") or row.get("publisher"),
                "url": row.get("url") or row.get("link"),
                "published": row.get("published") or row.get("published_at") or row.get("time"),
            }
        )
    return {"count": len(slim), "news": slim}


def _symbol_blob(data: dict[str, Any], symbol: str) -> dict[str, Any]:
    per = data.get("per_symbol") if isinstance(data.get("per_symbol"), dict) else {}
    by_symbol = per.get("by_symbol") or per.get("bySymbol") or {}
    if not isinstance(by_symbol, dict):
        return {}
    want = symbol.strip()
    alts = [
        want,
        want.replace("-", "/"),
        want.replace("/", ""),
        want.replace("/", "USDT") if "USDT" not in want else want,
    ]
    for key in alts:
        hit = by_symbol.get(key)
        if isinstance(hit, dict):
            return hit
    # case-insensitive
    want_n = want.upper().replace("-", "/")
    for k, v in by_symbol.items():
        if str(k).upper().replace("-", "/") == want_n and isinstance(v, dict):
            return v
    return {}


def _unwrap(node: Any) -> dict[str, Any]:
    if not isinstance(node, dict):
        return {}
    inner = node.get("data")
    if isinstance(inner, dict):
        return inner
    return node


def _candles_from_klines(raw: Any, *, limit: int = 90) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    rows = raw[-max(1, min(limit, 90)) :]
    out: list[dict[str, Any]] = []
    for row in rows:
        if isinstance(row, dict):
            ts = row.get("t") or row.get("ts") or row.get("time")
            out.append(
                {
                    "t": ts,
                    "o": row.get("o") or row.get("open"),
                    "h": row.get("h") or row.get("high"),
                    "l": row.get("l") or row.get("low"),
                    "c": row.get("c") or row.get("close"),
                    "v": row.get("v") or row.get("volume"),
                }
            )
            continue
        if isinstance(row, (list, tuple)) and len(row) >= 6:
            out.append(
                {
                    "t": row[0],
                    "o": row[1],
                    "h": row[2],
                    "l": row[3],
                    "c": row[4],
                    "v": row[5],
                }
            )
    return out


def get_historical_ohlcv(args: dict[str, Any], *, api_key: str) -> dict[str, Any]:
    """Daily (or snapshot) OHLCV + last funding + TA snapshot for one symbol."""
    as_of = str(args.get("as_of") or args.get("date") or "").strip()
    if not as_of:
        raise ValueError("as_of is required (YYYY-MM-DD)")
    symbol = _parse_symbols(args.get("symbol") or args.get("symbols") or "BTC/USDT")[0]
    try:
        limit = int(args.get("limit") or 90)
    except (TypeError, ValueError):
        limit = 90
    raw = datalayer_get(
        "/api/v1/historical/nexus",
        params={"as_of": as_of, "universe": symbol},
        api_key=api_key,
    )
    data = raw.get("data") if isinstance(raw, dict) else None
    if not isinstance(data, dict):
        raise RuntimeError("Datalayer snapshot missing data")
    blob = _symbol_blob(data, symbol)
    quant = _unwrap(blob.get("quant_summary"))
    ta = _unwrap(blob.get("technical_analysis"))
    funding = _unwrap(blob.get("funding"))
    coin = _unwrap(blob.get("coin"))
    candles = _candles_from_klines(quant.get("klines"), limit=limit)
    last_fr = funding.get("lastFundingRate")
    if last_fr is None:
        last_fr = (
            (quant.get("funding") or {}).get("lastFundingRate")
            if isinstance(quant.get("funding"), dict)
            else None
        )
    if last_fr is None:
        last_fr = coin.get("funding_rate")
    indicators = ta.get("indicators") if isinstance(ta.get("indicators"), dict) else {}
    return {
        "symbol": symbol,
        "as_of_date": data.get("as_of_date"),
        "interval": quant.get("interval") or ta.get("interval") or "1d",
        "last_close": (quant.get("ticker") or {}).get("lastPrice")
        if isinstance(quant.get("ticker"), dict)
        else ta.get("price"),
        "last_funding_rate": last_fr,
        "indicators": {
            k: indicators[k]
            for k in ("rsi", "sma_20", "sma_50", "ema_20", "macd", "atr")
            if k in indicators
        },
        "candles": candles,
        "candle_count": len(candles),
    }


def get_historical_funding(args: dict[str, Any], *, api_key: str) -> dict[str, Any]:
    as_of = str(args.get("as_of") or args.get("date") or "").strip()
    if not as_of:
        raise ValueError("as_of is required (YYYY-MM-DD)")
    symbol = _parse_symbols(args.get("symbol") or "BTC/USDT")[0]
    pack = get_historical_ohlcv({"as_of": as_of, "symbol": symbol, "limit": 1}, api_key=api_key)
    return {
        "symbol": pack["symbol"],
        "as_of_date": pack["as_of_date"],
        "last_funding_rate": pack.get("last_funding_rate"),
    }


def get_sentiment(args: dict[str, Any], *, api_key: str) -> dict[str, Any]:
    symbol = str(args.get("symbol") or "").strip()
    params: dict[str, str] = {}
    if symbol:
        params["symbol"] = symbol.replace("/USDT", "").replace("USDT", "")
    raw = datalayer_get("/api/sentiment", params=params or None, api_key=api_key)
    data = raw.get("data") if isinstance(raw, dict) else raw
    return _trim(data if data is not None else raw)


def _load_nexus(
    args: dict[str, Any], *, api_key: str
) -> tuple[str, str, dict[str, Any], dict[str, Any]]:
    as_of = str(args.get("as_of") or args.get("date") or "").strip()
    if not as_of:
        raise ValueError("as_of is required (YYYY-MM-DD)")
    symbol = _parse_symbols(args.get("symbol") or args.get("symbols") or "BTC/USDT")[0]
    raw = datalayer_get(
        "/api/v1/historical/nexus",
        params={"as_of": as_of, "universe": symbol},
        api_key=api_key,
    )
    data = raw.get("data") if isinstance(raw, dict) else None
    if not isinstance(data, dict):
        raise RuntimeError("Datalayer snapshot missing data")
    return as_of, symbol, data, _symbol_blob(data, symbol)


def get_open_interest(args: dict[str, Any], *, api_key: str) -> dict[str, Any]:
    as_of, symbol, data, blob = _load_nexus(args, api_key=api_key)
    coin = _unwrap(blob.get("coin"))
    quant = _unwrap(blob.get("quant_summary"))
    deriv = quant.get("derivatives") if isinstance(quant.get("derivatives"), dict) else {}
    oi = coin.get("oi") if isinstance(coin.get("oi"), dict) else {}
    binance = oi.get("binance") if isinstance(oi.get("binance"), dict) else {}
    return {
        "symbol": symbol,
        "as_of_date": data.get("as_of_date"),
        "price": coin.get("price"),
        "open_interest": binance.get("current_oi") or deriv.get("open_interest"),
        "open_interest_usd": binance.get("current_oi_usd") or deriv.get("open_interest_usd"),
        "long_short_ratio": coin.get("long_short_ratio") or deriv.get("account_ls_ratio"),
        "funding_rate": coin.get("funding_rate"),
    }


def get_vcp(args: dict[str, Any], *, api_key: str) -> dict[str, Any]:
    as_of, symbol, data, blob = _load_nexus(args, api_key=api_key)
    vcp = blob.get("vcp") if isinstance(blob.get("vcp"), dict) else {}
    gates = vcp.get("trend_template") if isinstance(vcp.get("trend_template"), list) else []
    passed = [g.get("name") for g in gates if isinstance(g, dict) and g.get("passed")]
    failed = [g.get("name") for g in gates if isinstance(g, dict) and not g.get("passed")]
    uni = data.get("vcp_universe") if isinstance(data.get("vcp_universe"), dict) else {}
    return {
        "symbol": symbol,
        "as_of_date": data.get("as_of_date"),
        "last_close": vcp.get("last_close"),
        "scan_tf": vcp.get("scan_tf") or uni.get("scan_tf"),
        "passed": passed,
        "failed": failed,
        "universe": {
            "n_scanned": uni.get("n_tokens_scanned"),
            "n_passed_strict": uni.get("n_passed_strict"),
            "n_passed_relaxed": uni.get("n_passed_relaxed"),
        },
    }


def get_macro(args: dict[str, Any], *, api_key: str) -> dict[str, Any]:
    as_of = str(args.get("as_of") or args.get("date") or "").strip()
    if not as_of:
        raise ValueError("as_of is required (YYYY-MM-DD)")
    raw = datalayer_get(
        "/api/v1/historical/nexus",
        params={"as_of": as_of, "universe": "BTC/USDT"},
        api_key=api_key,
    )
    data = raw.get("data") if isinstance(raw, dict) else None
    if not isinstance(data, dict):
        raise RuntimeError("Datalayer snapshot missing data")
    endpoints = data.get("endpoints") if isinstance(data.get("endpoints"), dict) else {}
    overview = _unwrap(endpoints.get("market_overview"))
    keys = (
        "fear_greed_index",
        "fear_greed_label",
        "vix",
        "us_10y_yield_pct",
        "effective_fed_funds_pct",
        "trade_weighted_usd_index",
        "sp500_index",
        "wti_crude_usd_per_bbl",
        "stablecoin_circulating_usd",
        "all_chain_tvl_usd",
        "onchain_liquidity_score",
    )
    return {
        "as_of_date": data.get("as_of_date"),
        "macro": {k: overview.get(k) for k in keys if overview.get(k) is not None},
    }


def get_historical_coverage(args: dict[str, Any], *, api_key: str) -> dict[str, Any]:
    raw = datalayer_get("/api/v1/historical/date-range", api_key=api_key)
    data = raw.get("data") if isinstance(raw, dict) else raw
    return _trim(data if data is not None else raw)


def get_etf_flow(args: dict[str, Any], *, api_key: str) -> dict[str, Any]:
    raw = datalayer_get("/api/etf/inflow", api_key=api_key)
    rows = raw.get("data") if isinstance(raw, dict) else raw
    if not isinstance(rows, list):
        return _trim(raw)
    try:
        limit = int(args.get("limit") or 40)
    except (TypeError, ValueError):
        limit = 40
    slim = rows[-max(1, min(limit, 90)) :]
    return {"count": len(slim), "inflow": _trim(slim)}


def get_oi_ranking(args: dict[str, Any], *, api_key: str) -> dict[str, Any]:
    try:
        limit = int(args.get("limit") or 10)
    except (TypeError, ValueError):
        limit = 10
    limit = max(1, min(limit, 25))
    raw = datalayer_get(
        "/api/oi/top-ranking",
        params={"limit": str(limit)},
        api_key=api_key,
    )
    data = raw.get("data") if isinstance(raw, dict) else raw
    positions = []
    if isinstance(data, dict):
        inner = data.get("data") if isinstance(data.get("data"), dict) else data
        positions = inner.get("positions") or inner.get("ranking") or []
    elif isinstance(data, list):
        positions = data
    if not isinstance(positions, list):
        positions = []
    slim = []
    for row in positions[:limit]:
        if not isinstance(row, dict):
            continue
        slim.append(
            {
                "symbol": row.get("symbol"),
                "rank": row.get("rank"),
                "oi_usd": row.get("oi_usd") or row.get("open_interest_usd"),
                "funding_rate": row.get("funding_rate"),
                "score": row.get("score"),
            }
        )
    return {"count": len(slim), "positions": slim}
