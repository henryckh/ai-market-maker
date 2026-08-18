"""Backtest package.

CLI (only public entry)::

    python -m backtest run …
    python -m backtest windows …

Core loop: ``loop.run_multi_step_backtest`` → ``BacktestEngine``.
Agent topology: deploy JSON only.
"""
