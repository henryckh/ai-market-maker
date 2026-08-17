"""Single backtest CLI — one engine, deploy JSON selects agents.

  python -m backtest run      …   # one period (continuous bars)
  python -m backtest windows  …   # multi-window historical report

``session`` is a deprecated alias for ``run`` (kept so old scripts still work).

Examples::

    NEXUS_DISABLE=1 python -m backtest run \\
      --deploy config/deploy.ohlcv_only.json --ticker BTC/USDT --steps 40 --csv-only

    NEXUS_DISABLE=1 python -m backtest windows \\
      --deploy config/deploy.active.json \\
      --suite release --ticker BTC/USDT --forward-validate
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _cmd_run(argv: list[str]) -> int:
    from dotenv import load_dotenv

    from backtest.config import resolve_backtest_config, set_env_from_config
    from backtest.session import build_run_parser, execute_run
    from config.app_settings import apply_strategy_env_defaults_from_settings, load_app_settings
    from config.llm_env import require_llm_key

    load_dotenv(override=True)
    os.environ["NEXUS_DISABLE"] = "1"
    os.environ["MODE"] = "backtest"
    apply_strategy_env_defaults_from_settings(load_app_settings())

    parser = build_run_parser()
    args = parser.parse_args(argv)
    if getattr(args, "quality", False):
        if not args.steps or args.steps < 200:
            args.steps = 200
        if getattr(args, "min_trades", 0) < 30:
            args.min_trades = 30
        if getattr(args, "tp_sl_pct", 0) <= 0:
            args.tp_sl_pct = 5.0
        args.forward_validate = True
        if os.environ.get("AIMM_BACKTEST_VERBOSE_RECEIPTS") is None:
            os.environ["AIMM_BACKTEST_VERBOSE_RECEIPTS"] = "1"
        print(
            "[quality] preset: --steps 200 --min-trades 30 --tp-sl-pct 5 --forward-validate",
            file=sys.stderr,
        )

    cli_mode = args.mode
    if args.llm and cli_mode is None:
        cli_mode = "agent_llm"

    bt_cfg = resolve_backtest_config(
        deploy_path=args.deploy,
        cli_arbitrator_mode=cli_mode,
        cli_tp_sl_pct=args.tp_sl_pct if args.tp_sl_pct and args.tp_sl_pct > 0 else None,
        cli_leverage=getattr(args, "leverage", None),
    )
    set_env_from_config(bt_cfg)
    if bt_cfg.get("use_llm"):
        require_llm_key()

    out = execute_run(args, parser, deploy_config=bt_cfg)
    actual_lev = bt_cfg.get("leverage")
    try:
        sp = Path(out.get("summary_path", ""))
        if sp.is_file():
            actual_lev = json.loads(sp.read_text(encoding="utf-8")).get("leverage", actual_lev)
    except Exception:
        pass
    out["resolved_config"] = {
        "arbitrator_mode": bt_cfg.get("arbitrator_mode"),
        "deploy_loaded": bt_cfg.get("deploy_loaded"),
        "deploy_path": bt_cfg.get("deploy_path"),
        "profile_id": bt_cfg.get("profile_id"),
        "profile_weights": bt_cfg.get("profile_weights", {}),
        "deploy_description": bt_cfg.get("deploy_description") or "",
        "take_profit_pct": bt_cfg.get("take_profit_pct"),
        "stop_loss_pct": bt_cfg.get("stop_loss_pct"),
        "leverage": actual_lev,
        "source_description": bt_cfg.get("source_description"),
        "agent_led_symbols": bt_cfg.get("agent_led_symbols", []),
    }
    print(json.dumps(out, indent=2), flush=True)
    return 0


def _cmd_windows(argv: list[str]) -> int:
    import argparse

    from dotenv import load_dotenv

    from backtest.config import resolve_backtest_config, set_env_from_config
    from backtest.historical_eval import (
        DEFAULT_DAILY_WINDOWS,
        LLM_MONTHLY_WINDOWS,
        RELEASE_DAILY_WINDOWS,
        report_to_markdown,
        run_suite,
    )
    from config.app_settings import load_app_settings

    load_dotenv(override=True)
    os.environ["NEXUS_DISABLE"] = "1"
    os.environ["MODE"] = "backtest"

    ticker_def = load_app_settings().market.default_ticker
    p = argparse.ArgumentParser(
        prog="python -m backtest windows",
        description="Multi-window historical backtest (same engine as run)",
    )
    p.add_argument("--suite", choices=("daily", "release", "llm_monthly"), default="release")
    p.add_argument("--llm", action="store_true")
    p.add_argument(
        "--mode",
        default=None,
        choices=("agent_llm", "weighted_convergence"),
        help="Arbitrator mode override (not the CLI top-level mode)",
    )
    p.add_argument("--deploy", nargs="?", const="config/deploy.active.json", default=None)
    p.add_argument("--ticker", default=ticker_def)
    p.add_argument("--exchange", default="binance")
    p.add_argument("--initial-cash", type=float, default=10_000.0)
    p.add_argument("--runs-dir", type=Path, default=Path(".runs"))
    p.add_argument("--max-windows", type=int, default=None)
    p.add_argument("--tp-sl-pct", type=float, default=None)
    p.add_argument("--forward-validate", action="store_true")
    p.add_argument("--forward-oos-bars", type=int, default=30)
    p.add_argument("--quality", action="store_true")
    p.add_argument("--online", action="store_true")
    p.add_argument("--cache-dir", type=Path, default=Path("data/ohlcv"))
    args = p.parse_args(argv)

    if args.quality:
        args.forward_validate = True

    bt_cfg = resolve_backtest_config(
        deploy_path=args.deploy,
        cli_arbitrator_mode=args.mode,
        cli_tp_sl_pct=args.tp_sl_pct,
    )
    if args.llm and args.mode is None:
        bt_cfg["arbitrator_mode"] = "agent_llm"
        bt_cfg["use_llm"] = True
    set_env_from_config(bt_cfg)

    use_llm = bool(bt_cfg.get("use_llm"))
    if use_llm:
        from config.llm_env import require_llm_key

        require_llm_key()
    try:
        llm_max = max(2, int((os.getenv("AIMM_BACKTEST_LLM_MAX_STEPS") or "120").strip(), 10))
    except ValueError:
        llm_max = 120

    windows = DEFAULT_DAILY_WINDOWS
    if args.suite == "llm_monthly":
        windows = LLM_MONTHLY_WINDOWS
    elif args.suite == "release":
        windows = RELEASE_DAILY_WINDOWS
    if args.max_windows is not None:
        windows = tuple(windows[: max(1, int(args.max_windows))])

    report = run_suite(
        windows,
        ticker=str(args.ticker),
        exchange=str(args.exchange),
        initial_cash=float(args.initial_cash),
        runs_dir=args.runs_dir,
        use_llm=use_llm,
        llm_max_steps=llm_max,
        deploy_profile_weights=bt_cfg.get("profile_weights"),
        deploy_profile_id=bt_cfg.get("profile_id"),
        deploy_arbitrator_mode=bt_cfg.get("arbitrator_mode"),
        take_profit_pct=bt_cfg.get("take_profit_pct", 0.0),
        stop_loss_pct=bt_cfg.get("stop_loss_pct", 0.0),
        max_hold_bars=bt_cfg.get("max_hold_bars", 0),
        forward_validate=bool(args.forward_validate),
        forward_oos_bars=int(args.forward_oos_bars),
        deploy_config=bt_cfg,
        csv_only=not bool(args.online),
        cache_dir=args.cache_dir,
    )
    report["resolved_config"] = {
        "arbitrator_mode": bt_cfg.get("arbitrator_mode"),
        "deploy_path": bt_cfg.get("deploy_path"),
        "profile_id": bt_cfg.get("profile_id"),
        "profile_weights": bt_cfg.get("profile_weights", {}),
        "deploy_description": bt_cfg.get("deploy_description") or "",
    }
    rp = Path(report["report_path"])
    rp.with_suffix(".md").write_text(report_to_markdown(report), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "windows"}, indent=2))
    print(json.dumps(report.get("aggregate") or {}, indent=2))
    print(f"\nFull report: {rp}", file=sys.stderr)
    print(f"Markdown:    {rp.with_suffix('.md')}", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__ or "", file=sys.stderr)
        return 0

    cmd, rest = argv[0], argv[1:]
    if cmd in ("run", "session"):
        return _cmd_run(rest)
    if cmd == "windows":
        return _cmd_windows(rest)

    print(
        f"unknown command {cmd!r}. Use:\n"
        "  python -m backtest run …        # one period\n"
        "  python -m backtest windows …    # multi-window report",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
