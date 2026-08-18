"""Release windows keep full calendar bars; suite sample size rolls up trades."""

from __future__ import annotations

from pathlib import Path

from backtest.historical_eval import (
    RELEASE_DAILY_WINDOWS,
    WINDOW_2021_H2,
    WINDOW_2022_H1,
    WINDOW_2025_H1,
    build_aggregate,
    run_window,
)
from backtest.validation import generate_quality_report


def test_release_windows_are_bull_bear_later_oos() -> None:
    ids = [w.id for w in RELEASE_DAILY_WINDOWS]
    assert ids == ["2021_h2", "2022_h1", "2025_h1"]
    assert RELEASE_DAILY_WINDOWS[0] is WINDOW_2021_H2
    assert RELEASE_DAILY_WINDOWS[1] is WINDOW_2022_H1
    assert RELEASE_DAILY_WINDOWS[2] is WINDOW_2025_H1


def test_windows_do_not_chop_llm_bars(monkeypatch) -> None:
    bars = [[1_600_000_000_000 + i * 86_400_000, 1, 2, 0.5, 1.0, 1.0] for i in range(180)]

    class _Res:
        run_id = "bt_x"
        steps = 180
        final_equity = 10_000.0
        metrics: dict = {}
        benchmark: dict = {}
        summary_path = trades_path = equity_path = None
        iterations_path = None

    monkeypatch.setattr("backtest.historical_eval._load_window_bars", lambda *a, **k: bars)
    monkeypatch.setattr(
        "backtest.historical_eval.run_multi_step_backtest",
        lambda **k: _Res(),
    )
    monkeypatch.setattr("backtest.historical_eval.load_trades_file", lambda p: [])

    out = run_window(
        WINDOW_2021_H2,
        ticker="BTC/USDT",
        exchange="binance",
        initial_cash=10_000.0,
        runs_dir=Path("/tmp"),
        eval_tag="eval_test",
        use_llm=True,
        llm_max_steps=70,
        csv_only=True,
    )
    assert out["bars_used"] == 180


def test_suite_sample_size_uses_total_trades() -> None:
    windows = [
        {
            "bars_used": 180,
            "total_return_pct": 1.0,
            "execution": {"fills": 6},
            "quality": {
                "overall_passed": True,
                "sample_size": {"warning": None, "min_bars_ok": True},
            },
            "metrics": {"sharpe": 1.0, "profit_factor": 1.5},
        },
        {
            "bars_used": 181,
            "total_return_pct": -0.5,
            "execution": {"fills": 5},
            "quality": {
                "overall_passed": True,
                "sample_size": {"warning": None, "min_bars_ok": True},
            },
            "metrics": {"sharpe": 0.2, "profit_factor": 1.1},
        },
        {
            "bars_used": 181,
            "total_return_pct": 2.0,
            "execution": {"fills": 5},
            "quality": {
                "overall_passed": True,
                "sample_size": {"warning": None, "min_bars_ok": True},
            },
            "metrics": {"sharpe": 0.8, "profit_factor": 1.4},
        },
    ]
    agg = build_aggregate(windows)
    ss = agg["quality"]["suite_sample_size"]
    assert ss["total_trades"] == 16
    assert ss["min_bars_ok"] is True
    assert ss["min_trades_ok"] is True
    assert ss["passed"] is True
    assert agg["quality"]["all_windows_passed"] is True


def test_window_quality_can_pass_on_bars_alone() -> None:
    closes = [100.0 + i * 0.4 for i in range(120)]
    report = generate_quality_report(
        close_prices=closes,
        total_bars=120,
        trade_count=4,
        profit_factor=1.5,
        trades=[{"exit_reason": "signal"} for _ in range(4)],
        require_min_trades=False,
    )
    assert report.sample_size.min_bars_ok
    assert not report.sample_size.min_trades_ok
    assert report.sample_size.passed is False
