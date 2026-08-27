"""Quantitative finance modules for the AIMM backtest engine.

Agentic Architecture (not traditional quant):
  - Each agent desk is a specialist producing independent opinions
  - Desks run in batch (cross-sectional, parallel) for efficiency
  - The hedge fund pipeline (debate → arbitrator → risk guard) decides BUY/SELL/HOLD
  - Full P&L attribution shows which desks drove every dollar of return

Modules:
  - agentic_arbitrator.py   — batch all desks per bar, LLM + deterministic inference
  - attribution.py          — desk-level P&L decomposition, provably trustworthy equity curves
  - batch_signal_adapter.py — plug into PerpEngine: batch exec + hedge fund pipeline"""
