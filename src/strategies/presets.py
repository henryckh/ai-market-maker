"""Named Research presets — weighted arbitrator / agent_llm desk profiles.

These map to ``config/deploy.*.json`` profiles. The runtime is always the
multi-agent graph (Tier-0 desks → weighted arbitrator → risk/execution),
not classic EMA/RSI rule packs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

DEFAULT_QUANT_STRATEGY_ID = "macro_tilt"


@dataclass(frozen=True)
class StrategyPreset:
    id: str
    title: str
    description: str
    category: str = "weighted"
    coin: str = ""
    reasoning_preview: str = ""
    #: Deploy JSON used for weights + arbitrator mode (agent_llm).
    deploy_path: str = "config/deploy.active.json"
    n_bars: int = 220
    interval_sec: int = 86_400  # 1d — matches profitable agentic windows
    max_steps: int = 200
    seed: int = 1
    fee_bps: float = 10.0
    initial_cash: float = 100_000.0
    #: Suggested lookback days when the UI opens a date range.
    lookback_days: int = 60
    #: Optional multi-symbol demo basket (comma-separated). Empty = single ticker UI.
    symbols: str = ""


PRESETS: dict[str, StrategyPreset] = {
    "macro_tilt": StrategyPreset(
        id="macro_tilt",
        title="Top earner (g49 news CoT)",
        category="weighted",
        coin="layers",
        deploy_path="config/deploy.active.json",
        reasoning_preview=(
            "1. Tier-0 desks emit directional scores (TA, news, macro, pattern, funding). "
            "2. Weights: TA 0.35 CoT, news 0.20 CoT, macro 0.20, pattern 0.15, stat 0.10. "
            "3. Per-agent LLM refines intent on TA + news desks. "
            "4. Weighted arbitrator fuses composites + TA-led gates. "
            "5. Portfolio + risk guard size and veto. "
            "6. Leverage 2.0. Rank-1 earner on the scored book (+8.15% / 1.71)."
        ),
        description=(
            "Shipped default (g49). Highest mean on the scored catalog: +8.15% / Sharpe 1.71."
        ),
        n_bars=220,
        interval_sec=86_400,
        max_steps=200,
        lookback_days=60,
        symbols="BTC/USDT,ETH/USDT,SOL/USDT",
        seed=1,
    ),
    "ohlcv_only": StrategyPreset(
        id="ohlcv_only",
        title="OHLCV-led desk",
        category="weighted",
        coin="chart",
        deploy_path="config/deploy.ohlcv_only.json",
        reasoning_preview=(
            "1. Emphasize OHLCV/TA desk (2.3:0.75) with pattern assist (2.1:0.25). "
            "2. No desk CoT and no arbitrator LLM — numeric fusion only. "
            "3. Useful when macro inputs are noisy or you need an offline smoke. "
            "4. Same execution stack — only the desk weight profile changes."
        ),
        description=(
            "No-LLM smoke: TA + pattern only (ohlcv_only). Use to check CSV wiring, "
            "not as a return preset."
        ),
        n_bars=200,
        interval_sec=86_400,
        max_steps=180,
        lookback_days=60,
        symbols="BTC/USDT,ETH/USDT",
        seed=3,
    ),
}


def get_preset(preset_id: str) -> StrategyPreset:
    key = (preset_id or "").strip() or DEFAULT_QUANT_STRATEGY_ID
    # Legacy Research card ids → current weighted default.
    legacy = {
        "momentum": "macro_tilt",
        "mean_reversion": "macro_tilt",
        "conservative_gate": "macro_tilt",
        "all_weather": "macro_tilt",
    }
    key = legacy.get(key, key)
    if key not in PRESETS:
        raise KeyError(f"Unknown strategy preset {key!r}. Known: {list(PRESETS)}")
    return PRESETS[key]


def list_presets() -> list[dict[str, Any]]:
    return [
        {
            "id": p.id,
            "title": p.title,
            "description": p.description,
            "category": p.category,
            "reasoning_preview": p.reasoning_preview,
            "deploy_path": p.deploy_path,
            "symbols": p.symbols,
            "defaults": {
                "n_bars": p.n_bars,
                "interval_sec": p.interval_sec,
                "max_steps": p.max_steps,
                "seed": p.seed,
                "fee_bps": p.fee_bps,
                "initial_cash": p.initial_cash,
                "lookback_days": p.lookback_days,
            },
        }
        for p in PRESETS.values()
    ]


def merge_preset_quick_request(
    preset_id: str,
    *,
    ticker: str | None = None,
    exchange_id: str | None = None,
    n_bars: int | None = None,
    interval_sec: int | None = None,
    max_steps: int | None = None,
    seed: int | None = None,
    fee_bps: float | None = None,
    initial_cash: float | None = None,
    since_iso: str | None = None,
    until_iso: str | None = None,
) -> dict[str, Any]:
    """Build kwargs compatible with :func:`backtest_routes.post_quick_backtest` body."""
    p = get_preset(preset_id)
    out: dict[str, Any] = {
        "ticker": ticker or "BTC/USDT",
        "n_bars": n_bars if n_bars is not None else p.n_bars,
        "interval_sec": interval_sec if interval_sec is not None else p.interval_sec,
        "max_steps": max_steps if max_steps is not None else p.max_steps,
        "seed": seed if seed is not None else p.seed,
        "fee_bps": fee_bps if fee_bps is not None else p.fee_bps,
        "initial_cash": initial_cash if initial_cash is not None else p.initial_cash,
        "deploy_path": p.deploy_path,
        "preset_id": p.id,
        "symbols": p.symbols,
    }
    if exchange_id is not None:
        ex = str(exchange_id).strip()
        if ex:
            out["exchange_id"] = ex
    s = (since_iso or "").strip()
    u = (until_iso or "").strip()
    if s and u:
        out["since_iso"] = s
        out["until_iso"] = u
    return out


def quant_trace_meta() -> dict[str, Any]:
    """Embedded in Quant agent JSON for UI / operator transparency."""
    p = PRESETS[DEFAULT_QUANT_STRATEGY_ID]
    return {
        "preset_id": p.id,
        "title": p.title,
        "arbitrator_mode": "agent_llm",
        "deploy_path": p.deploy_path,
        "signals": [
            "Tier-0 desks (TA / pattern / macro)",
            "Per-agent LLM under agent_llm mode",
            "Weighted arbitrator + TA-led gates",
            "Portfolio proposal + risk veto",
        ],
        "portfolio": "Weighted multi-agent desk (macro_tilt) — not classic EMA/RSI rules.",
    }
