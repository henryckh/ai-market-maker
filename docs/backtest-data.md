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
- **monetary_sentinel** — OHLCV window return/vol → synthetic `market_overview` (no look-ahead)

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
uv run python -m backtest.run_demo \
  --deploy config/deploy.ohlcv_only.json --ticker-only --steps 20 --csv-only
```
