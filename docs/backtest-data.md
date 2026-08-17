# Backtest data strategy

## What ships today

| Layer | Source | Offline? |
|-------|--------|----------|
| OHLCV | `data/ohlcv/*.csv` via `prefetch_ohlcv` / `bootstrap_showcase` | Yes |
| LLM decisions | `.cache/decisions/` (prompt hash, `MODE=backtest`) | Yes (warm reruns) |
| Tier-0 macro/pattern context | `ohlcv_derived_context` injected in backtest engine | Yes (from same bars) |

Run once before demos:

```bash
uv run python -m backtest.bootstrap_showcase
```

## Default desk combo (`macro_tilt`)

`config/deploy.active.json` — `technical_ta_engine`, `pattern_recognition_bot`, `monetary_sentinel`.

All three work in backtest without live Nexus:

- **technical_ta_engine / pattern_recognition_bot** — OHLCV + TA-Lib Tier-0
- **monetary_sentinel** — FRED + DefiLlama lag-1 + Fear & Greed on `market_overview` (no look-ahead); OHLCV return/vol if those CSVs are missing

Set `AIMM_BACKTEST_OHLCV_NEXUS=0` to disable synthetic Nexus context.

## Nexus-heavy agents (later PR)

Agents `news_narrative_miner`, `statistical_alpha_engine`, `retail_hype_tracker`, `pro_bias_analyst`, `whale_behavior_analyst`, `liquidity_order_flow` need feeds that are not reconstructible from OHLCV alone. Omit them from deploy JSON so they are not added to the graph.

**Recommended path (not blocking showcase):**

1. **This PR** — OHLCV cache + derived context for macro_tilt (3 desks).
2. **Next PR** — `prefetch_nexus_snapshot` storing a *current* API bundle under `data/nexus/` for paper/live smoke tests (not historical replay).
3. **Future** — coordinate with Nexus team for **bar-aligned historical bundles** or accept recorded JSONL replay per backtest window.

Do not wire live Nexus per bar in backtest — it would inject look-ahead (today's news on 2022 bars).

## OHLCV-only two-desk mode

For fastest smoke tests: `config/deploy.ohlcv_only.json` (technical_ta_engine + pattern_recognition_bot only).

```bash
NEXUS_DISABLE=1 uv run python -m backtest run \
  --deploy config/deploy.ohlcv_only.json --ticker-only --steps 20 --csv-only
```

## Paper offline pipeline (Phases A–D)

Scripts live under `scripts/data/` (see `scripts/data/README.md`).

| Step | Command | Output |
|------|---------|--------|
| OHLCV 1d history | `uv run python scripts/data/prefetch_ohlcv_history.py` | `data/ohlcv/*_1d.csv`, `data/MANIFEST.json` |
| Fear & Greed | `uv run python scripts/data/fetch_fear_greed.py` | `data/macro/fear_greed_daily.csv` |
| FRED macro | pinned `data/macro/fred_daily.csv` (VIX, fed funds, 10y, DXY) | `monetary_sentinel` overlay |
| DefiLlama liquidity | pinned `data/macro/defillama_liquidity_daily.csv` (lag-1) | `monetary_sentinel` overlay |
| CryptoVision daily | Download [Mendeley wvjjxr8bxx](https://data.mendeley.com/datasets/wvjjxr8bxx/2) → `data/news_sentiment/raw/`, then `uv run python scripts/data/build_cryptovision_daily.py` | `data/news_sentiment/daily_by_coin.csv` |
| Nexus fixtures | `uv run python scripts/data/build_nexus_fixtures.py` | `data/fixtures/nexus_daily.jsonl` |

**Local historical lookup (not a network API):** `HistoricalNexusProvider` (via `market_scan`) returns bar-date-aligned offline context. The engine only stamps `window_last_ts_ms`.

Primary paper timeframe: **1d**. Optional 1h via `--timeframe 1h` on the prefetch script only if needed for denser trade stats.

## Nexus providers (live vs historical)

Backtests must not call the live Skills API (look-ahead / non-reproducible).

| Mode | Provider | Module |
|------|----------|--------|
| paper / live | `LiveNexusProvider` | `nexus_data.live_provider` |
| backtest | `HistoricalNexusProvider` | `nexus_data.historical.provider` |

Factory: `nexus_data.provider.resolve_nexus_provider(run_mode=...)`.

`market_scan` attaches `shared_memory["nexus"]` from the selected provider. The
backtest engine only stamps `window_last_ts_ms` (as-of clock); it does not build
ad-hoc nexus payloads.

Binance Vision bulk download:

```bash
uv run python scripts/data/prefetch_binance_vision.py --tier core --since 2021-01
```
