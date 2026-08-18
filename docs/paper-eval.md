# Backtest protocol

**One CLI. Deploy JSON selects agents.**

```bash
python -m backtest run …       # one period (continuous bars)
python -m backtest windows …   # multi-window historical report
```

```text
deploy JSON → resolve config → build_workflow(enabled desks)
           → BacktestEngine → HistoricalNexusProvider (offline)
           → fills / equity / report
```

## Catalog

`description` on each JSON is documentation only. It is stamped into `summary.json` → `resolved_config.deploy_description` and the HTML/markdown reports.

Shipped presets are the **top 10 unique earners** under `config/deploy.*.json` (clone prints that scored the same number were dropped). Research leftovers live in `config/experiments/` and `config/grid/` (gitignored). `deploy.ohlcv_only.json` is no-LLM CI wiring.

| Rank | Deploy | Mean / Sharpe |
|------|--------|----------------|
| 1 | `deploy.active.json` (g49) | **+8.15% / 1.71** |
| 2 | `deploy.easy_short.json` | +6.48% / 1.66 |
| 3 | `deploy.tight_sl.json` | +6.04% / 1.50 |
| 4 | `deploy.tp8.json` | +5.82% / 1.43 |
| 5 | `deploy.lev15.json` | +5.52% / 1.17 |
| 6 | `deploy.stat_cot.json` | +5.03% / 1.43 |
| 7 | `deploy.news_flow.json` | +4.13% / 1.02 |
| 8 | `deploy.swing_sharpe.json` | +4.13% / 0.87 |
| 9 | `deploy.ta_heavy.json` | +3.53% / 0.78 |
| 10 | `deploy.sharpe_focus.json` | +3.46% / 0.68 |

`swing_sharpe` is also the only file green on the locked 3-window `--suite release` (mean +4.76%).

## Where runs go

| What | Path |
|------|------|
| Shipped combinations | `config/deploy.<name>.json` |
| Local research copies | `config/experiments/` (gitignored) |
| Candidate grid | `config/grid/` (gitignored) |
| Catalog results | `.runs/catalog/<name>/evaluations/` |
| Catalog index | `.runs/catalog/index.json` |

```bash
uv run python scripts/run_catalog.py
```

Runs every `config/deploy.*.json` on the locked `--suite release` book. The number to quote is `deploy.active.json`.

`run_agentic_sweep.py` is a leftover weight grid. Do not use it for prompt or LLM combo search.

## LLM / prompts

Backtest defaults `AIMM_AGENT_PROMPTS_PATH` to `config/agent_prompts.active.json` (active trader overlay). Desk CoT uses persona.md + SKILL.md, not that JSON. Operator JSON **appends** to the engineered arbitrator rules; it does not replace them.

```bash
python -m backtest run \
  --deploy config/deploy.active.json \
  --ticker BTC/USDT --steps 40 --csv-only --llm
```

## Examples

```bash
export NEXUS_DISABLE=1

python -m backtest run \
  --deploy config/deploy.ohlcv_only.json \
  --ticker BTC/USDT --steps 40 --csv-only

python -m backtest windows \
  --deploy config/deploy.active.json \
  --suite release --ticker BTC/USDT --forward-validate
```

Pinned data under `data/` + same deploy ⇒ comparable runs.
