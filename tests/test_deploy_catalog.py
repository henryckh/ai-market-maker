"""Shipped catalog is the top earners plus a no-LLM smoke."""

from __future__ import annotations

import json
from pathlib import Path

from backtest.config import resolve_backtest_config

ROOT = Path(__file__).resolve().parents[1]
DEPLOY_DIR = ROOT / "config"

# Ranked by scored-book mean return (unique outcomes only). Smoke is last.
SHIPPED = (
    "deploy.active.json",  # rank 1 g49
    "deploy.easy_short.json",
    "deploy.lev15.json",
    "deploy.news_flow.json",
    "deploy.ohlcv_only.json",
    "deploy.sharpe_focus.json",
    "deploy.stat_cot.json",
    "deploy.swing_sharpe.json",
    "deploy.ta_heavy.json",
    "deploy.tight_sl.json",
    "deploy.tp8.json",
)

PROFILE_IDS = {
    "deploy.active.json": "g49_tilt",
    "deploy.easy_short.json": "easy_short",
    "deploy.tight_sl.json": "tight_sl",
    "deploy.tp8.json": "tp8",
    "deploy.lev15.json": "lev15",
    "deploy.stat_cot.json": "stat_cot",
    "deploy.news_flow.json": "news_flow",
    "deploy.swing_sharpe.json": "swing_sharpe",
    "deploy.ta_heavy.json": "ta_heavy",
    "deploy.sharpe_focus.json": "sharpe_focus",
    "deploy.ohlcv_only.json": "ohlcv_only",
}


def test_every_deploy_has_description() -> None:
    files = sorted(DEPLOY_DIR.glob("deploy.*.json"))
    names = [p.name for p in files]
    assert names == sorted(SHIPPED), names
    for path in files:
        obj = json.loads(path.read_text(encoding="utf-8"))
        assert isinstance(obj.get("description"), str) and obj["description"].strip(), path.name
        assert isinstance(obj.get("agents"), dict) and obj["agents"], path.name
        assert isinstance(obj.get("execution"), dict), path.name
        assert obj.get("profile", {}).get("profile_id") == PROFILE_IDS[path.name], path.name


def test_default_deploy_is_rank_one_earner() -> None:
    cfg = resolve_backtest_config()
    assert cfg["deploy_loaded"] is True
    assert cfg["profile_id"] == "g49_tilt"
    assert cfg["profile_weights"]["news_narrative_miner"] == 0.2
    assert cfg["profile_weights"]["statistical_alpha_engine"] == 0.1
    assert cfg["leverage"] == 2.0
    assert cfg["use_llm"] is True


def test_swing_sharpe_still_loads() -> None:
    cfg = resolve_backtest_config(deploy_path=str(DEPLOY_DIR / "deploy.swing_sharpe.json"))
    assert cfg["profile_id"] == "swing_sharpe"
    assert cfg["take_profit_pct"] == 8.0
    assert cfg["stop_loss_pct"] == 2.5


def test_ohlcv_only_is_no_llm_smoke() -> None:
    smoke = resolve_backtest_config(deploy_path=str(DEPLOY_DIR / "deploy.ohlcv_only.json"))
    assert smoke["profile_id"] == "ohlcv_only"
    assert smoke["use_llm"] is False


def test_universe_drops_matic_and_pol() -> None:
    tiers = json.loads((DEPLOY_DIR / "universe_tiers.json").read_text(encoding="utf-8"))
    joined = " ".join(tiers.get("core", []) + tiers.get("portfolio", []))
    assert "MATIC" not in joined
    assert "POLUSDT" not in joined
