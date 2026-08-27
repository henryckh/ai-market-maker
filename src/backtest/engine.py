"""Backtest engine — batch agentic desks per bar via PerpEngine.

All 9 agent desk opinions are computed cross-sectionally per bar, arbitrated
with dynamic weights (rolling Sharpe), sized through inverse-vol portfolio
optimization, and decomposed via per-desk P&L attribution.

Perp only (spot removed as of v1.0). Config via dict (no env vars).
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List
from typing import Any as _Any

from config.app_settings import load_app_settings
from harness.run_memory import IterationReceiptWriter, RunWorkingMemory, run_memory_config

from .perp_engine_runner import run_perp_backtest

logger = logging.getLogger(__name__)


@dataclass
class BacktestConfig:
    """Backward-compatible config dataclass (perp only).

    Maps to PerpEngine dict config internally.
    """

    initial_cash_usd: float = 10_000.0
    initial_btc: float = 0.0
    fee_bps: float = 10.0
    slippage_bps: float = 5.0
    interval_sec: int = 300
    max_steps: int | None = None
    progress_callback: _Any | None = None
    runs_dir: _Any | None = None
    export_bundle: bool = True
    min_bars_between_trades: int = 0
    instrument: str = "perp"
    leverage: float = 3.0
    take_profit_pct: float = 0.0
    stop_loss_pct: float = 0.0
    max_hold_bars: int = 0
    timeframe: str = ""
    run_id: str = ""


class BacktestEngine:
    """Backtest entry point — batch agentic desks, perp only as of v1.0.

    Usage::

        engine = BacktestEngine(config)
        result = engine.run("BTC/USDT", bars=bars_list)
    """

    def __init__(self, config: BacktestConfig | dict | None = None):
        if isinstance(config, BacktestConfig):
            self._cfg = {
                "initial_cash_usd": config.initial_cash_usd,
                "initial_btc": config.initial_btc,
                "fee_bps": config.fee_bps,
                "slippage_bps": config.slippage_bps,
                "interval_sec": config.interval_sec,
                "max_steps": config.max_steps,
                "progress_callback": config.progress_callback,
                "runs_dir": config.runs_dir,
                "export_bundle": config.export_bundle,
                "instrument": config.instrument,
                "leverage": config.leverage,
                "take_profit_pct": config.take_profit_pct,
                "stop_loss_pct": config.stop_loss_pct,
                "max_hold_bars": config.max_hold_bars,
                "deploy_profile_weights": getattr(config, "deploy_profile_weights", None),
                "deploy_profile_id": getattr(config, "deploy_profile_id", None),
                "deploy_arbitrator_mode": getattr(config, "deploy_arbitrator_mode", None),
                "timeframe": config.timeframe,
                "run_id": config.run_id,
            }
        else:
            self._cfg = dict(config or {})

    def run(
        self,
        ticker: str = "BTC/USDT",
        bars: List[List[Any]] | None = None,
        bars_by_symbol: Dict[str, List[List[Any]]] | None = None,
        run_id: str | None = None,
        runs_dir: Path | None = None,
    ) -> Dict[str, Any]:
        """Run a perpetual backtest with batch agentic desk execution.

        All 9 agent desks are invoked cross-sectionally once per bar.
        Results include full per-desk P&L attribution.
        """
        if bars is not None and bars_by_symbol is not None:
            raise ValueError("provide bars OR bars_by_symbol, not both")
        if bars_by_symbol is None:
            if bars is None:
                raise ValueError("provide bars or bars_by_symbol")
            bars_by_symbol = {ticker: list(bars)}
        if bars is not None and ticker not in bars_by_symbol:
            bars_by_symbol[ticker] = list(bars)

        c = self._cfg
        run_id = run_id or c.get("run_id") or f"bt_{int(time.time())}"
        c["run_id"] = run_id
        cfg_rd = c.get("runs_dir")
        if cfg_rd is None:
            cfg_rd = ".runs"
        runs_dir = runs_dir or (cfg_rd if isinstance(cfg_rd, Path) else Path(cfg_rd))
        self._init_logging(run_id, runs_dir)

        from backtest.terminal_log import (
            configure_backtest_terminal_logging,
            print_run_header,
            print_run_summary,
        )

        configure_backtest_terminal_logging()

        bt_dir = runs_dir / "backtests" / run_id
        bt_dir.mkdir(parents=True, exist_ok=True)
        iterations_path = bt_dir / "iterations.jsonl"
        receipt_writer = IterationReceiptWriter(path=iterations_path)

        from config.fund_policy import load_fund_policy

        fp = load_fund_policy()
        settings = load_app_settings()
        from backtest.ta_warmup import resolve_ta_warmup_bars

        ta_warmup = resolve_ta_warmup_bars(
            override=(
                int(c["ta_warmup_bars"])
                if c.get("ta_warmup_bars") is not None
                else (
                    int(c["min_warmup_bars"])
                    if c.get("min_warmup_bars") is not None
                    else int(settings.backtest.min_warmup_bars or 0) or None
                )
            )
        )
        c["min_warmup_bars"] = ta_warmup
        bar_count = max(len(rows) for rows in bars_by_symbol.values())
        eval_steps = int(c.get("eval_steps") or max(2, bar_count - ta_warmup))

        # Extract TP/SL from deploy JSON execution block (if not already set at top level)
        _deploy = c.get("deploy_config") if isinstance(c.get("deploy_config"), dict) else {}
        _exec = _deploy.get("execution") if isinstance(_deploy.get("execution"), dict) else {}
        tp = float(c.get("take_profit_pct") or _exec.get("take_profit_pct") or 0.0)
        sl = float(c.get("stop_loss_pct") or _exec.get("stop_loss_pct") or 0.0)
        lev = float(c.get("leverage") or _exec.get("leverage") or fp.max_leverage)
        slip_bps = float(c.get("slippage_bps") or _exec.get("slippage_bps") or 5.0)

        perp_cfg = {
            "initial_cash": float(c.get("initial_cash_usd", 10_000)),
            "leverage": lev,
            "taker_rate": float(c.get("fee_bps", 10.0)) / 10_000,
            "maker_rate": float(c.get("fee_bps", 10.0)) / 10_000,
            "slippage": slip_bps / 10_000,
            "funding_rate": 0.0001,
            "interval_sec": int(c.get("interval_sec", 300)),
            "take_profit_pct": tp,
            "stop_loss_pct": sl,
            "max_hold_bars": int(c.get("max_hold_bars", 0)),
            "trade_cooldown_bars": int(c.get("trade_cooldown_bars", fp.trade_cooldown_bars)),
            "timeframe": str(c.get("timeframe", "")),
            "eval_start_bar": ta_warmup,
        }

        print_run_header(
            run_id=run_id,
            symbols=list(bars_by_symbol.keys()),
            total_bars=bar_count,
            profile_id=str(c.get("deploy_profile_id") or ""),
            profile_weights=c.get("deploy_profile_weights")
            if isinstance(c.get("deploy_profile_weights"), dict)
            else None,
            ta_warmup_bars=ta_warmup,
            eval_bars=eval_steps,
            use_llm=bool(
                c.get("use_llm")
                or c.get("arbitrator_mode") == "agent_llm"
                or c.get("deploy_arbitrator_mode") == "agent_llm"
            ),
        )

        run_mem = RunWorkingMemory(cfg=run_memory_config(settings))

        # ---- Batch agentic desk execution ----
        logger.info("Batch agentic mode — cross-sectional desk execution per bar")
        from quant.batch_signal_adapter import batch_signal_factory

        # Resolve nexus provider for historical data
        nexus_provider = None
        try:
            from nexus_data.provider import resolve_nexus_provider

            nexus_provider = resolve_nexus_provider(run_mode="backtest")
        except Exception:
            pass

        signal_fn = batch_signal_factory(
            bars_by_symbol=bars_by_symbol,
            symbols=list(bars_by_symbol.keys()),
            config=c,
            nexus_context_provider=nexus_provider,
            receipt_writer=receipt_writer,
            run_id=run_id,
            run_mem=run_mem,
        )

        result = run_perp_backtest(
            ticker=ticker,
            bars_by_symbol=bars_by_symbol,
            signal_fn=signal_fn,
            config=perp_cfg,
            run_id=run_id,
            runs_dir=runs_dir,
            progress_callback=c.get("progress_callback"),
        )

        # ---- Attach attribution summary ----
        attr_obj = getattr(signal_fn, "attribution", None)
        if attr_obj is not None:
            attr_summary = attr_obj.summary()
            result["attribution"] = attr_summary
            result["desk_pnl"] = attr_summary.get("desk_cumulative_pnl", {})
            result["desk_sharpes"] = attr_summary.get("desk_sharpe_ratios", {})

        # Write attribution JSONL
        try:
            attr_path = bt_dir / "attribution.jsonl"
            with open(attr_path, "w") as f:
                for line in attr_obj.to_jsonl():
                    f.write(line + "\n")
            result.setdefault("paths", {})["attribution"] = str(attr_path)
        except Exception:
            pass

        # Write attribution summary JSON
        try:
            attr_summary_path = bt_dir / "attribution_summary.json"
            with open(attr_summary_path, "w") as f:
                json.dump(attr_summary, f, indent=2)
            result.setdefault("paths", {})["attribution_summary"] = str(attr_summary_path)
        except Exception:
            pass

        m = result.get("metrics", {})
        events_path = runs_dir / f"{run_id}.events.jsonl"
        bench_raw = result.get("benchmark")
        bench_out: dict[str, Any] = dict(bench_raw) if isinstance(bench_raw, dict) else {}
        paths = {
            "summary": str(runs_dir / "backtests" / run_id / "summary.json"),
            "trades": str(runs_dir / "backtests" / run_id / "trades.jsonl"),
            "equity": str(runs_dir / "backtests" / run_id / "equity.jsonl"),
            "iterations": str(iterations_path),
            "events": str(events_path) if events_path.exists() else str(events_path),
            "attribution": result.get("paths", {}).get("attribution", ""),
            "attribution_summary": result.get("paths", {}).get("attribution_summary", ""),
        }
        print_run_summary(
            run_id=result.get("run_id", run_id),
            metrics=m,
            benchmark=bench_out,
            trade_count=int(m.get("total_trades", 0)),
            steps=eval_steps,
            paths=paths,
        )
        return {
            "run_id": result.get("run_id", run_id),
            "steps": eval_steps,
            "eval_bars": eval_steps,
            "ta_warmup_bars": ta_warmup,
            "total_bars": int(result.get("total_bars", bar_count)),
            "interval_sec": int(c.get("interval_sec", 300)),
            "trade_count": m.get("total_trades", 0),
            "metrics": m,
            "final_equity": result.get("final_equity", perp_cfg["initial_cash"]),
            "benchmark": bench_out,
            "paths": paths,
            "attribution": result.get("attribution", {}),
            "desk_pnl": result.get("desk_pnl", {}),
            "desk_sharpes": result.get("desk_sharpes", {}),
        }

    @staticmethod
    def _init_logging(run_id: str, runs_dir: Path) -> None:
        from flow_log import FlowEventRepo, set_flow_repo

        lp = runs_dir / f"{run_id}.events.jsonl"
        if lp.exists():
            lp.unlink()
        try:
            (runs_dir / "latest_backtest.txt").write_text(run_id)
        except Exception:
            pass
        flow_repo = FlowEventRepo(run_id=run_id, log_path=lp)
        set_flow_repo(flow_repo)
