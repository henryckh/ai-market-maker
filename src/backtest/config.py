"""Backtest config: merge deploy JSON and CLI overrides."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Mapping

logger = logging.getLogger(__name__)

DEFAULT_DEPLOY_PATH = "config/deploy.active.json"

ARBITRATOR_AGENT_LLM = "agent_llm"
ARBITRATOR_WEIGHTED_CONVERGENCE = "weighted_convergence"

VALID_ARBITRATOR_MODES = (ARBITRATOR_AGENT_LLM, ARBITRATOR_WEIGHTED_CONVERGENCE)


def resolve_tp_sl_pct(
    *,
    cli_tp_sl_pct: float | None = None,
    deploy_execution: Mapping[str, Any] | None = None,
    fund_policy: Any | None = None,
) -> tuple[float, float]:
    """Resolve TP/SL in engine percent units (6.0 = 6%). CLI > deploy > fund policy."""
    if cli_tp_sl_pct is not None and float(cli_tp_sl_pct) > 0:
        v = float(cli_tp_sl_pct)
        return v, v

    exec_cfg = deploy_execution if isinstance(deploy_execution, Mapping) else {}
    tp_raw = exec_cfg.get("take_profit_pct")
    sl_raw = exec_cfg.get("stop_loss_pct")
    if tp_raw is not None or sl_raw is not None:
        # Deploy stores fractions (0.025); engine uses percent (2.5).
        tp = float(tp_raw) * 100.0 if tp_raw is not None else 0.0
        sl = float(sl_raw) * 100.0 if sl_raw is not None else 0.0
        return tp, sl

    fp = fund_policy
    if fp is None:
        try:
            from config.fund_policy import load_fund_policy

            fp = load_fund_policy()
        except (FileNotFoundError, OSError, ValueError) as e:
            logger.warning("fund policy unavailable for TP/SL fallback: %s", e)
            return 0.0, 0.0
    tp = float(fp.take_profit_pct) * 100.0
    sl_frac = float(getattr(fp, "stop_loss_pct", 0.0) or 0.0)
    # Mirror TP when policy stop_loss is unset.
    sl = sl_frac * 100.0 if sl_frac > 0 else tp
    return tp, sl


def resolve_backtest_config(
    *,
    deploy_path: str | None = None,
    cli_arbitrator_mode: str | None = None,
    cli_tp_sl_pct: float | None = None,
    cli_leverage: float | None = None,
    cli_max_hold_bars: int | None = None,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Merge deploy config, environment, and CLI overrides."""
    if env is None:
        env = dict(os.environ)

    from backtest.agentic_defaults import (
        DEFAULT_AGENTIC_PROFILE_ID,
        default_agentic_decision_threshold,
        default_agentic_profile_weights,
    )

    result: dict[str, Any] = {
        "arbitrator_mode": ARBITRATOR_WEIGHTED_CONVERGENCE,
        "profile_weights": default_agentic_profile_weights(),
        "profile_id": DEFAULT_AGENTIC_PROFILE_ID,
        "decision_threshold": default_agentic_decision_threshold(),
        "allows_short": True,
        "use_llm": False,
        "take_profit_pct": 0.0,
        "stop_loss_pct": 0.0,
        "leverage": 2.0,
        "max_hold_bars": 0,
        "deploy_path": "",
        "deploy_loaded": False,
        "source_description": "defaults",
    }

    # Optional: which deploy file to load (path only — not strategy knobs).
    # An explicit deploy_path argument wins over the env alias.
    env_deploy_path = (env.get("AIMM_DEPLOY_CONFIG_PATH") or "").strip()
    if not deploy_path and env_deploy_path:
        deploy_path = env_deploy_path

    deploy_cfg: dict[str, Any] | None = None
    deploy_execution: Mapping[str, Any] | None = None
    effective_deploy_path = deploy_path or DEFAULT_DEPLOY_PATH
    deploy_file = Path(effective_deploy_path)
    if deploy_file.is_file():
        try:
            deploy_cfg = json.loads(deploy_file.read_text(encoding="utf-8"))
            if not isinstance(deploy_cfg, dict):
                deploy_cfg = None
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("failed to read deploy config at %s: %s", effective_deploy_path, e)
            deploy_cfg = None

    if deploy_cfg is not None:
        result["deploy_path"] = str(deploy_file.resolve())
        result["deploy_loaded"] = True

        exec_cfg = (
            deploy_cfg.get("execution") if isinstance(deploy_cfg.get("execution"), dict) else {}
        )
        deploy_execution = exec_cfg

        use_llm = exec_cfg.get("use_llm_synthesis")
        if use_llm is None:
            legacy = str(exec_cfg.get("arbitrator_mode") or "").strip().lower()
            use_llm = legacy in ("agent_llm", "llm", "full_agentic")
        if use_llm:
            result["arbitrator_mode"] = ARBITRATOR_AGENT_LLM
            result["use_llm"] = True
        else:
            result["arbitrator_mode"] = ARBITRATOR_WEIGHTED_CONVERGENCE
            result["use_llm"] = False

        agents = deploy_cfg.get("agents")
        if isinstance(agents, dict) and agents:
            weights: dict[str, float] = {}
            for name, meta in agents.items():
                if not isinstance(meta, dict) or meta.get("enabled", True) is False:
                    continue
                try:
                    weights[str(name)] = float(meta.get("weight") or 0.0)
                except (TypeError, ValueError):
                    continue
            if weights:
                positive = {k: v for k, v in weights.items() if v > 0}
                if positive:
                    result["profile_weights"] = positive

        dt = deploy_cfg.get("decision_threshold")
        if isinstance(dt, dict) and dt:
            result["decision_threshold"] = dict(dt)

        if exec_cfg.get("allows_short") is not None:
            result["allows_short"] = bool(exec_cfg.get("allows_short"))

        profile = deploy_cfg.get("profile")
        if isinstance(profile, dict) and profile.get("profile_id"):
            result["profile_id"] = str(profile["profile_id"])

        lev = exec_cfg.get("leverage") or result["leverage"]
        mhb = exec_cfg.get("max_hold_bars") or result["max_hold_bars"]
        result["leverage"] = float(lev) if lev else 2.0
        result["max_hold_bars"] = int(mhb) if mhb else 0

    # CLI can still pick a deploy mode for a single experiment run
    if cli_arbitrator_mode is not None and cli_arbitrator_mode.strip():
        mode = cli_arbitrator_mode.strip().lower()
        if mode in VALID_ARBITRATOR_MODES:
            result["arbitrator_mode"] = mode
            result["use_llm"] = mode == ARBITRATOR_AGENT_LLM
        else:
            logger.warning("unknown arbitrator mode %r, ignoring", mode)

    tp_pct, sl_pct = resolve_tp_sl_pct(
        cli_tp_sl_pct=cli_tp_sl_pct,
        deploy_execution=deploy_execution,
    )
    result["take_profit_pct"] = tp_pct
    result["stop_loss_pct"] = sl_pct

    if cli_leverage is not None and cli_leverage >= 1.0:
        result["leverage"] = float(cli_leverage)

    if cli_max_hold_bars is not None and cli_max_hold_bars > 0:
        result["max_hold_bars"] = int(cli_max_hold_bars)

    from backtest.symbol_routing import resolve_agent_led_symbols

    result["agent_led_symbols"] = sorted(
        resolve_agent_led_symbols(deploy_cfg=deploy_cfg if deploy_cfg else None)
    )

    parts = []
    if result["deploy_loaded"]:
        parts.append(f"deploy:{result['deploy_path']}")
    parts.append(f"mode:{result['arbitrator_mode']}")
    parts.append(f"tp:{result['take_profit_pct']}")
    parts.append(f"lev:{result['leverage']}")
    if cli_arbitrator_mode is not None:
        parts.append("cli-override")
    result["source_description"] = "|".join(parts)

    return result


def set_env_from_config(cfg: dict[str, Any]) -> None:
    """Apply non-strategy process settings for a backtest run."""
    os.environ.pop("AIMM_ARBITRATOR_MODE", None)
    os.environ.pop("AI_MARKET_MAKER_USE_LLM", None)
    os.environ.pop("AIMM_LLM_MODE", None)
    os.environ.pop("AIMM_LLM_AGENTS", None)
    os.environ.pop("AIMM_LLM_DESK_DEBATE", None)
    os.environ["MODE"] = "backtest"

    if cfg.get("deploy_path"):
        os.environ["AIMM_DEPLOY_CONFIG_PATH"] = str(cfg["deploy_path"])

    if os.environ.get("AIMM_BACKTEST_VERBOSE_RECEIPTS") is None:
        os.environ["AIMM_BACKTEST_VERBOSE_RECEIPTS"] = "1"

    from backtest.terminal_log import configure_backtest_terminal_logging

    configure_backtest_terminal_logging()

    if cfg.get("deploy_loaded"):
        os.environ["AIMM_DEPLOY_ACTIVE"] = "1"


def available_arbitrator_modes() -> list[str]:
    return list(VALID_ARBITRATOR_MODES)


__all__ = [
    "ARBITRATOR_AGENT_LLM",
    "ARBITRATOR_WEIGHTED_CONVERGENCE",
    "VALID_ARBITRATOR_MODES",
    "available_arbitrator_modes",
    "resolve_backtest_config",
    "resolve_tp_sl_pct",
    "set_env_from_config",
]
