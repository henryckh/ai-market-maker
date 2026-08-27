"""Fund policy — single source of truth for deployment trading policy.

Loads from ``config/policy.default.json`` (or ``AIMM_POLICY_CONFIG_PATH`` env override).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

# ================================================================
# Policy dataclass
# ================================================================


@dataclass(frozen=True)
class FundPolicy:
    """Per-deployment trading policy (hedge-fund-style knobs)."""

    portfolio_budget_usd: float
    stop_loss_pct: float
    take_profit_pct: float
    max_leverage: float
    min_confidence_directional: float

    # Order sizing caps (per-symbol add / scale-in).
    order_max_add_btc: float
    order_max_add_notional_usd: float | None

    #: Upper bound on each symbol's position_size in agents.risk_management (allocation weights).
    risk_position_cap_usd: float

    #: execution_intent reporting: cash * this * max_leverage (engine uses max_leverage separately).
    intent_notional_fraction: float

    # Rule overlay thresholds (Tier-1 tape / quant bridge).
    rule_sentiment_buy_min: float
    rule_sentiment_sell_below: float

    #: When False, bearish thesis on a flat book does not emit SELL (long-only / no short).
    allows_short: bool

    #: Minimum bars between fills (per-symbol). Used in both backtest and live to prevent churn.
    trade_cooldown_bars: int

    # Exposure shaping (regime-aware).
    bull_min_target_fraction: float
    bear_max_target_fraction: float

    # Portfolio-level circuit breaker.
    risk_max_drawdown_stop: float | None
    risk_kill_switch_cooldown_bars: int


# ================================================================
# Policy loading
# ================================================================


def _load_policy_config_from_json(path: str | None) -> dict[str, Any]:
    """Load a JSON policy config file."""
    if not path:
        raise ValueError("policy path is required")
    p = Path(path).expanduser()
    if not p.exists() or not p.is_file():
        raise FileNotFoundError(f"policy file not found: {p}")
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        raise ValueError(f"failed to parse policy JSON: {p}") from e
    if not isinstance(obj, dict):
        raise ValueError(f"policy JSON must be an object: {p}")
    return obj


def _policy_from_mapping(overrides: Mapping[str, Any]) -> FundPolicy:
    """Build a FundPolicy from JSON mapping.

    Supported JSON shapes:
    - {"policy": {...}}
    - {...} (policy keys at top-level)
    """
    o = overrides.get("policy") if isinstance(overrides.get("policy"), dict) else overrides
    if not isinstance(o, dict):
        raise ValueError("policy JSON must be an object or contain a 'policy' object")

    def f(key: str) -> float | None:
        v = o.get(key)
        if v is None:
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    def i(key: str) -> int | None:
        v = o.get(key)
        if v is None:
            return None
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return None

    def b(key: str) -> bool | None:
        v = o.get(key)
        if v is None:
            return None
        if isinstance(v, bool):
            return v
        if isinstance(v, (int, float)):
            return bool(int(v))
        s = str(v).strip().lower()
        if s in ("1", "true", "yes", "on", "y"):
            return True
        if s in ("0", "false", "no", "off", "n"):
            return False
        return None

    def opt_f(key: str) -> float | None:
        v = o.get(key)
        if v is None:
            return None
        try:
            x = float(v)
        except (TypeError, ValueError):
            return None
        return x

    portfolio_budget_usd = f("portfolio_budget_usd")
    stop_loss_pct = f("stop_loss_pct")
    take_profit_pct = f("take_profit_pct")
    max_leverage = f("max_leverage")
    min_conf = f("min_confidence_directional")
    order_max_add_btc = f("order_max_add_btc")
    risk_position_cap_usd = f("risk_position_cap_usd")
    intent_notional_fraction = f("intent_notional_fraction")
    rule_buy_min = f("rule_sentiment_buy_min")
    rule_sell_below = f("rule_sentiment_sell_below")
    allows_short = b("allows_short")
    trade_cooldown_bars = i("trade_cooldown_bars")
    bull_floor = f("bull_exposure_floor")
    bear_cap = f("bear_exposure_cap")
    risk_dd = f("risk_max_drawdown_stop")
    kill_cd = i("risk_kill_switch_cooldown_bars")

    missing = [
        name
        for name, val in (
            ("portfolio_budget_usd", portfolio_budget_usd),
            ("stop_loss_pct", stop_loss_pct),
            ("take_profit_pct", take_profit_pct),
            ("max_leverage", max_leverage),
            ("min_confidence_directional", min_conf),
            ("order_max_add_btc", order_max_add_btc),
            ("risk_position_cap_usd", risk_position_cap_usd),
            ("intent_notional_fraction", intent_notional_fraction),
            ("rule_sentiment_buy_min", rule_buy_min),
            ("rule_sentiment_sell_below", rule_sell_below),
            ("allows_short", allows_short),
            ("trade_cooldown_bars", trade_cooldown_bars),
            ("bull_exposure_floor", bull_floor),
            ("bear_exposure_cap", bear_cap),
            ("risk_max_drawdown_stop", risk_dd),
            ("risk_kill_switch_cooldown_bars", kill_cd),
        )
        if val is None
    ]
    if missing:
        raise ValueError(f"policy JSON missing required keys: {', '.join(missing)}")

    order_max_add_notional_usd = opt_f("order_max_add_notional_usd")
    if "order_max_add_notional_usd" in o and o.get("order_max_add_notional_usd") is None:
        order_max_add_notional_usd = None

    return FundPolicy(
        portfolio_budget_usd=max(100.0, float(portfolio_budget_usd)),
        stop_loss_pct=max(0.0, min(0.95, float(stop_loss_pct))),
        take_profit_pct=max(0.0, float(take_profit_pct)),
        max_leverage=max(1.0, min(100.0, float(max_leverage))),
        min_confidence_directional=max(0.0, min(1.0, float(min_conf))),
        order_max_add_btc=max(1e-6, float(order_max_add_btc)),
        order_max_add_notional_usd=(
            max(0.0, float(order_max_add_notional_usd))
            if order_max_add_notional_usd is not None
            else None
        ),
        risk_position_cap_usd=max(100.0, float(risk_position_cap_usd)),
        intent_notional_fraction=max(0.0, min(1.0, float(intent_notional_fraction))),
        rule_sentiment_buy_min=max(0.0, min(100.0, float(rule_buy_min))),
        rule_sentiment_sell_below=max(0.0, min(100.0, float(rule_sell_below))),
        allows_short=bool(allows_short),
        trade_cooldown_bars=max(0, int(trade_cooldown_bars)),
        bull_min_target_fraction=max(0.0, min(1.0, float(bull_floor))),
        bear_max_target_fraction=max(0.0, min(1.0, float(bear_cap))),
        risk_max_drawdown_stop=(
            max(0.0, min(0.95, float(risk_dd))) if float(risk_dd) > 0 else None
        ),
        risk_kill_switch_cooldown_bars=max(0, int(kill_cd)),
    )


def load_fund_policy(*, path: Path | None = None) -> FundPolicy:
    """Load the canonical FundPolicy from config/policy.default.json (or an explicit path).

    This is intentionally strict: missing/invalid policy should fail fast.
    """
    if path is None:
        env_path = (
            os.getenv("AIMM_CONFIG_PATH") or os.getenv("AIMM_POLICY_CONFIG_PATH") or ""
        ).strip()
        if env_path:
            path = Path(env_path)
    p = path or Path("config/policy.default.json")
    obj = _load_policy_config_from_json(str(p))
    return _policy_from_mapping(obj)


__all__ = ["FundPolicy", "load_fund_policy"]
