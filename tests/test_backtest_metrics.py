from backtest.historical_eval import build_aggregate, summarize_execution_trades
from backtest.metrics import (
    compute_basic_metrics,
    max_drawdown,
    returns_from_equity,
    sharpe_ratio,
    win_rate,
)


def test_max_drawdown_basic():
    assert max_drawdown([100, 110, 105, 120, 90, 130]) == (120 - 90) / 120


def test_returns_from_equity():
    assert returns_from_equity([100, 110, 99]) == [0.1, -0.1]


def test_sharpe_zero_when_flat():
    assert sharpe_ratio([0.0, 0.0, 0.0]) == 0.0


def test_sharpe_capped_when_sample_vol_near_zero():
    """Many identical bars then one small move → sample std ~0; Sharpe must stay bounded."""
    eq = [10000.0] * 50 + [9980.0]
    rets = returns_from_equity(eq)
    s = sharpe_ratio(rets, periods_per_year=105_120)
    assert -15.0 <= s <= 15.0
    assert abs(s) < 1000.0


def test_win_rate():
    assert win_rate([1, -1, 2, 0]) == 0.5


def test_compute_basic_metrics_smoke():
    m = compute_basic_metrics(
        equity_curve=[100, 110, 105, 115],
        trade_pnls=[1, -1, 0.5],
        interval_sec=86_400,
    )
    assert m.max_drawdown > 0
    assert isinstance(m.sharpe, float)
    assert isinstance(m.sortino, float)
    assert 0 <= m.win_rate <= 1
    assert m.periods_per_year >= 300
    assert m.profit_factor is not None


def test_summarize_execution_trades_counts_direction_round_trips():
    trades = [{"direction": 1, "pnl": 12.0}, {"direction": -1, "pnl": -4.0}]
    summary = summarize_execution_trades(trades)
    assert summary["buy_fills"] == 1
    assert summary["sell_fills"] == 1
    assert summary["fills"] == 2
    assert summary["opened_position"] is True
    assert summary["round_trip_evidence"] is True


def test_summarize_execution_trades_still_reads_side():
    trades = [{"side": "buy"}, {"side": "SELL"}]
    summary = summarize_execution_trades(trades)
    assert summary["buy_fills"] == 1
    assert summary["sell_fills"] == 1


def test_aggregate_includes_mean_sharpe():
    windows = [
        {
            "total_return_pct": 9.5,
            "metrics": {"sharpe": 2.5, "max_drawdown_pct": 4.5, "profit_factor": 3.6},
            "benchmark": {
                "benchmark_buy_hold_equity_return_pct": -24.0,
                "excess_return_vs_buy_hold_equity_pct": 33.5,
            },
            "execution": {},
            "quality": {},
        },
        {
            "total_return_pct": 3.3,
            "metrics": {"sharpe": 0.8, "max_drawdown_pct": 4.0, "profit_factor": 1.6},
            "benchmark": {
                "benchmark_buy_hold_equity_return_pct": -50.0,
                "excess_return_vs_buy_hold_equity_pct": 53.3,
            },
            "execution": {},
            "quality": {},
        },
    ]
    agg = build_aggregate(windows)
    assert agg["windows_beat_buy_hold_equity"] == 2
    assert agg["mean_sharpe"] == 1.65
    assert agg["mean_max_drawdown_pct"] == 4.25
    assert agg["mean_profit_factor"] == 2.6
