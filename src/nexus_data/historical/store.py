"""Pinned offline store: OHLCV, funding, news fixtures, Fear & Greed, FRED, DefiLlama.

No network. Safe for as-of lookups (caller must pass bar time).
"""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from nexus_data.historical.catalog import ccxt_to_vision_symbol, data_root


def ms_to_utc_date(ts_ms: float | int) -> str:
    return datetime.fromtimestamp(float(ts_ms) / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d")


@lru_cache(maxsize=2)
def _nexus_fixture_index(path_str: str) -> dict[str, dict[str, Any]]:
    path = Path(path_str)
    out: dict[str, dict[str, Any]] = {}
    if not path.is_file():
        return out
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            day = str(obj.get("date") or "")
            if day:
                out[day] = obj
    return out


def load_fixture_for_date(date: str, *, root: Path | None = None) -> dict[str, Any] | None:
    base = root or data_root()
    path = base / "fixtures" / "nexus_daily.jsonl"
    row = _nexus_fixture_index(str(path)).get(date)
    if not row:
        return None
    return {k: v for k, v in row.items() if k != "date"}


@lru_cache(maxsize=4)
def _funding_by_symbol(path_str: str) -> dict[str, list[tuple[int, float]]]:
    """symbol vision id -> sorted (ts_ms, rate) ascending."""
    path = Path(path_str)
    out: dict[str, list[tuple[int, float]]] = {}
    if not path.is_file():
        return out
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                sym = (row.get("symbol") or "").strip().upper()
                ts = int(float(row["timestamp_ms"]))
                rate = float(row["funding_rate"])
            except (KeyError, TypeError, ValueError):
                continue
            out.setdefault(sym, []).append((ts, rate))
    for sym in out:
        out[sym].sort(key=lambda x: x[0])
    return out


def funding_as_of(
    symbol: str,
    as_of_ms: int,
    *,
    root: Path | None = None,
) -> float | None:
    """Latest funding rate with timestamp <= as_of_ms."""
    base = root or data_root()
    # Prefer combined file; fall back to per-symbol
    combined = base / "derivatives" / "funding_all.csv"
    series_map = _funding_by_symbol(str(combined)) if combined.is_file() else {}
    vid = ccxt_to_vision_symbol(symbol)
    series = series_map.get(vid)
    if series is None:
        per = base / "derivatives" / f"funding_{vid}.csv"
        if per.is_file():
            series_map = _funding_by_symbol(str(per))
            series = series_map.get(vid) or next(iter(series_map.values()), None)
    if not series:
        return None
    lo, hi = 0, len(series) - 1
    best: float | None = None
    while lo <= hi:
        mid = (lo + hi) // 2
        ts, rate = series[mid]
        if ts <= as_of_ms:
            best = rate
            lo = mid + 1
        else:
            hi = mid - 1
    return best


@lru_cache(maxsize=2)
def _fear_greed_index(path_str: str) -> dict[str, dict[str, Any]]:
    path = Path(path_str)
    out: dict[str, dict[str, Any]] = {}
    if not path.is_file():
        return out
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            day = (row.get("date") or "").strip()
            if not day:
                continue
            try:
                out[day] = {
                    "value": int(float(row.get("value") or 50)),
                    "label": row.get("label") or "",
                }
            except (TypeError, ValueError):
                continue
    return out


def load_fear_greed(date: str, *, root: Path | None = None) -> dict[str, Any] | None:
    base = root or data_root()
    path = base / "macro" / "fear_greed_daily.csv"
    return _fear_greed_index(str(path)).get(date)


def _opt_float(row: dict[str, str], *keys: str) -> float | None:
    for key in keys:
        raw = (row.get(key) or "").strip()
        if not raw:
            continue
        try:
            return float(raw)
        except ValueError:
            continue
    return None


@lru_cache(maxsize=2)
def _fred_index(path_str: str) -> dict[str, dict[str, Any]]:
    path = Path(path_str)
    out: dict[str, dict[str, Any]] = {}
    if not path.is_file():
        return out
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            day = (row.get("date") or "").strip()
            if not day:
                continue
            rec: dict[str, Any] = {}
            mapping = (
                ("vix", "vix"),
                ("us_10y_yield_pct", "us_10y_yield_pct"),
                ("effective_fed_funds_pct", "effective_fed_funds_pct"),
                ("trade_weighted_usd_index", "trade_weighted_usd_index"),
                ("sp500_index", "sp500_index"),
                ("wti_crude_usd_per_bbl", "wti_crude_usd_per_bbl"),
            )
            for src, dst in mapping:
                val = _opt_float(row, src)
                if val is not None:
                    rec[dst] = val
            if rec:
                rec["source"] = (row.get("source") or "fred_public_csv").strip()
                out[day] = rec
    return out


def load_fred(date: str, *, root: Path | None = None) -> dict[str, Any] | None:
    """FRED row for this UTC date. CSV already carries weekends/holidays."""
    base = root or data_root()
    path = base / "macro" / "fred_daily.csv"
    return _fred_index(str(path)).get(date)


@lru_cache(maxsize=2)
def _defillama_index(path_str: str) -> dict[str, dict[str, Any]]:
    path = Path(path_str)
    out: dict[str, dict[str, Any]] = {}
    if not path.is_file():
        return out
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            day = (row.get("date") or "").strip()
            if not day:
                continue
            rec: dict[str, Any] = {}
            mapping = (
                ("stablecoin_circulating_usd", "stablecoin_circulating_usd"),
                ("stablecoin_change_7d_pct", "stablecoin_change_7d_pct"),
                ("all_chain_tvl_usd", "all_chain_tvl_usd"),
                ("all_chain_tvl_change_7d_pct", "all_chain_tvl_change_7d_pct"),
            )
            for src, dst in mapping:
                val = _opt_float(row, src)
                if val is not None:
                    rec[dst] = val
            if rec:
                rec["source"] = (row.get("source") or "defillama_public_aggregate_lag1").strip()
                out[day] = rec
    return out


def load_defillama(date: str, *, root: Path | None = None) -> dict[str, Any] | None:
    """DefiLlama aggregates. CSV is tagged lag1 — lookup by bar date, do not shift again."""
    base = root or data_root()
    path = base / "macro" / "defillama_liquidity_daily.csv"
    return _defillama_index(str(path)).get(date)


def ohlcv_csv_path(symbol: str, timeframe: str = "1d", *, root: Path | None = None) -> Path:
    base = root or data_root()
    stem = symbol.strip().replace("/", "_").replace(":", "_")
    return base / "ohlcv" / f"{stem}_{timeframe}.csv"


__all__ = [
    "ms_to_utc_date",
    "load_fixture_for_date",
    "funding_as_of",
    "load_fear_greed",
    "load_fred",
    "load_defillama",
    "ohlcv_csv_path",
]
