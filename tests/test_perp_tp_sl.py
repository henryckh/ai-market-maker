"""Daily TP/SL: no same-bar round-trip; fill at the trigger from high/low."""

from __future__ import annotations

from backtest.engines.perp import PerpEngine


def _bars(*ohlc: tuple[float, float, float, float]) -> list[list[float]]:
    rows: list[list[float]] = []
    for i, (open_, high, low, close) in enumerate(ohlc):
        rows.append([1_600_000_000_000 + i * 86_400_000, open_, high, low, close, 10.0])
    return rows


def _long(_sym, window, _pos, _acct) -> float:
    return 1.0 if len(window) >= 1 else 0.0


def _short(_sym, window, _pos, _acct) -> float:
    return -1.0 if len(window) >= 1 else 0.0


def _engine(**extra: object) -> PerpEngine:
    cfg: dict = {
        "initial_cash": 10_000,
        "leverage": 1.0,
        "take_profit_pct": 6.0,
        "stop_loss_pct": 2.5,
        "slippage": 0.0,
        "taker_rate": 0.0,
        "maker_rate": 0.0,
        "funding_rate": 0.0,
    }
    cfg.update(extra)
    return PerpEngine(cfg)


def test_entry_bar_does_not_take_profit_on_close() -> None:
    """A +9% close on the fill bar used to ghost-TP at the close. Now it marks overnight."""
    bars = _bars(
        (100.0, 101.0, 99.0, 100.0),
        (100.0, 110.0, 99.5, 109.0),
        (109.0, 110.0, 108.0, 109.5),
    )
    engine = _engine()
    engine.run({"BTC/USDT": bars}, _long)
    assert engine.snapshots[1].position_count == 1
    assert all(t.holding_bars >= 1 for t in engine.trades)


def test_take_profit_fills_at_trigger_not_close() -> None:
    bars = _bars(
        (100.0, 101.0, 99.0, 100.0),
        (100.0, 101.0, 99.5, 100.5),
        (100.5, 120.0, 100.0, 118.0),
    )
    engine = _engine()
    engine.run({"BTC/USDT": bars}, _long)
    tp = next(t for t in engine.trades if t.exit_reason == "take_profit")
    assert tp.holding_bars >= 1
    expected = tp.entry_price * 1.06
    assert abs(tp.exit_price - expected) < 0.05
    assert tp.exit_price < 110.0


def test_intrabar_stop_beats_target() -> None:
    """Wide next bar hits both SL and TP — stop is filled (not the lucky close)."""
    bars = _bars(
        (100.0, 101.0, 99.0, 100.0),
        (100.0, 101.0, 99.5, 100.2),
        (100.2, 120.0, 90.0, 115.0),
    )
    engine = _engine()
    engine.run({"BTC/USDT": bars}, _long)
    last = engine.trades[-1]
    assert last.exit_reason == "stop_loss"
    expected = last.entry_price * 0.975
    assert abs(last.exit_price - expected) < 0.05


def test_short_does_not_keep_full_crash_close() -> None:
    """Jun-13 style: short, next day −15% close must not pay 15% when TP is 6%."""
    bars = _bars(
        (100.0, 101.0, 99.0, 100.0),
        (100.0, 101.0, 99.0, 99.5),
        (99.5, 100.0, 84.0, 85.0),
    )
    engine = _engine(leverage=1.5)
    engine.run({"BTC/USDT": bars}, _short)
    tp = next(t for t in engine.trades if t.exit_reason == "take_profit")
    expected = tp.entry_price * 0.94
    assert abs(tp.exit_price - expected) < 0.08
    assert tp.exit_price > 90.0
