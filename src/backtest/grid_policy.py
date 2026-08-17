"""Catalog skip policy: do not re-run known-bad or duplicate configs."""

from __future__ import annotations

# Exact stems already proven weak on the catalog (math 7-window or LLM 2-window).
# Recorded in CSV; never queued for another LLM spend.
SKIP_BAD: dict[str, str] = {
    "golden": "LLM 2-window mean -10.13% (0 profitable)",
    "hybrid": "LLM 2-window mean -10.13% (clone of golden)",
    "long_only": "LLM 2-window mean -6.15% (0 profitable)",
    "conservative_gate": "math 7-window mean -4.81%",
    "bear_defense": "LLM 2-window mean -3.84%",
    "ohlcv_only": "math 7-window mean -3.55%",
    "macro_tilt_news": "math 7-window mean -3.10% (news overlay hurt vs measurement)",
    "ta_only": "math 7-window mean -2.98%",
    "bull_trend": "LLM 2-window mean -1.18%",
    "06_aggressive_loose": "180d showcase loser; loose gates overtrade",
    "07_gating_strict": "too few fills; not an LLM combo",
    "11_showcase_macro": "lost on 180d showcase",
    "12_ta_heavy_arb": "lost on 180d showcase",
    "13_strict_plus_arb": "lost on 180d showcase",
    "15_winner_hybrid": "180d showcase -24%",
}

# Numbered experiments that are copies of first-class deploys.
SKIP_DUP: dict[str, str] = {
    "02_macro_tilt_full": "duplicate of experiments/deploy.macro_tilt",
    "03_arbitrator_only": "duplicate of experiments/deploy.golden",
    "10_long_only": "duplicate of experiments/deploy.long_only",
    "14_swing_hold": "duplicate of experiments/deploy.swing_hold",
    "16_sharpe_focus": "duplicate of experiments/deploy.sharpe_focus",
    "17_bear_defense": "duplicate of experiments/deploy.bear_defense",
    "18_bull_trend": "duplicate of experiments/deploy.bull_trend",
    "19_news_cot": "duplicate of experiments/deploy.news_cot",
    "20_swing_sharpe": "now shipped as deploy.active.json",
}

# Already ran in .runs/catalog — copy into the ledger, do not re-queue.
ALREADY_RAN: dict[str, str] = {
    "sharpe_focus": "catalog LLM 2w mean +3.46%",
    "swing_sharpe": "catalog LLM 2w mean +4.13% (best)",
    "swing_hold": "catalog LLM 2w mean +0.12%",
    "macro_tilt": "previous shipped default (TA×0.55); replaced by news_stat",
    "news_cot": "catalog LLM 2w mean +1.17%",
    "macro_tilt_measurement": "catalog math 7w mean +1.17% (best math; not LLM)",
}

# Unique experiments not covered by deploys and not on the skip list.
QUEUE_EXPERIMENTS: tuple[str, ...] = (
    "01_ta_desk_llm",
    "04_ta_pattern_tight",
    "05_macro_heavy",
    "08_pattern_llm",
    "09_five_desk",
)

GOOD_MEAN_PCT = 0.0
GOOD_MIN_PROFITABLE = 1


def skip_reason(stem: str) -> str | None:
    if stem in SKIP_BAD:
        return f"known_bad: {SKIP_BAD[stem]}"
    if stem in SKIP_DUP:
        return f"duplicate: {SKIP_DUP[stem]}"
    if stem in ALREADY_RAN:
        return f"already_ran: {ALREADY_RAN[stem]}"
    return None


def is_good_row(*, mean_pct: float | None, profitable_windows: int | None) -> bool:
    if mean_pct is None:
        return False
    return float(mean_pct) > GOOD_MEAN_PCT and int(profitable_windows or 0) >= GOOD_MIN_PROFITABLE
