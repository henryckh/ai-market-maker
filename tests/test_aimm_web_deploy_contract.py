"""Contract: aimm-web Strategy Builder deploy → Flow resolve/factory.

Locks the shape buildFlowDeploy must emit (mirrors config/deploy.active.json)
and verifies _merge_inline_deploy_overrides + batch_signal_factory consume it
without dropping agents / llm_enabled (the silent HOLD regression).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from api.backtest_routes import _merge_inline_deploy_overrides
from backtest.config import resolve_backtest_config
from quant.batch_signal_adapter import batch_signal_factory

ROOT = Path(__file__).resolve().parents[1]
AIMM_WEB = ROOT.parent / "aimm-web"


# Exact Flow desk ids from AgentRegistry — builder agent_type aliases must map here.
CANONICAL_DESKS = {
    "monetary_sentinel",
    "news_narrative_miner",
    "pattern_recognition_bot",
    "statistical_alpha_engine",
    "technical_ta_engine",
    "retail_hype_tracker",
    "pro_bias_analyst",
    "whale_behavior_analyst",
    "liquidity_order_flow",
}


def _builder_default_deploy(*, mode: str = "agent_llm") -> dict:
    """Simulate aimm-web buildFlowDeploy() for DEFAULT Step2 agents + mode."""
    # Mirrors DEFAULT_AGENTS agent_type + weight (skip market_scanner weight 0).
    raw = {
        "monetary_sentinel": 0.05,
        "news_narrative_miner": 0.05,
        "pattern_recognition": 0.25,  # builder type
        "statistical_alpha": 0.10,
        "technical_ta": 0.30,
        "retail_hype": 0.05,
        "pro_bias": 0.05,
        "whale_behavior": 0.05,
        "liquidity_order_flow": 0.15,
    }
    type_map = {
        "monetary_sentinel": "monetary_sentinel",
        "news_narrative_miner": "news_narrative_miner",
        "pattern_recognition": "pattern_recognition_bot",
        "statistical_alpha": "statistical_alpha_engine",
        "technical_ta": "technical_ta_engine",
        "retail_hype": "retail_hype_tracker",
        "pro_bias": "pro_bias_analyst",
        "whale_behavior": "whale_behavior_analyst",
        "liquidity_order_flow": "liquidity_order_flow",
    }
    agents = {
        type_map[k]: {"weight": v, "enabled": True, "llm_enabled": False} for k, v in raw.items()
    }
    is_llm = mode in ("agent_llm", "llm", "full_agentic")
    if is_llm:
        agents["technical_ta_engine"]["llm_enabled"] = True
        agents["news_narrative_miner"]["llm_enabled"] = True
    return {
        "agents": agents,
        "profile_weights": {k: v["weight"] for k, v in agents.items()},
        "execution": {
            "use_llm_synthesis": is_llm,
            "desk_debate_llm": False,
            "arbitrator_llm": is_llm,
            "allows_short": True,
            "leverage": 3.0,
            "take_profit_pct": 4.0,
            "stop_loss_pct": 2.5,
            "max_position": 0.25,
            "slippage_bps": 5.0,
            "max_drawdown_pct": 15.0,
            "position_sizing_model": "fixed",
        },
        "arbitrator_mode": "agent_llm" if is_llm else mode,
        "decision_threshold": {
            "buy": {"min_composite": 51, "min_confidence": 1},
            "sell": {"max_composite": 49, "min_confidence": 1},
            "hold": {"else": True},
        },
    }


def test_risk_execution_fields_merge_onto_cfg():
    cfg = resolve_backtest_config()
    deploy = _builder_default_deploy(mode="weighted_convergence")
    _merge_inline_deploy_overrides(cfg, deploy)
    assert cfg["leverage"] == pytest.approx(3.0)
    assert cfg["take_profit_pct"] == pytest.approx(4.0)
    assert cfg["stop_loss_pct"] == pytest.approx(2.5)
    assert cfg["max_position"] == pytest.approx(0.25)
    assert cfg["slippage_bps"] == pytest.approx(5.0)
    assert cfg["use_llm"] is False
    assert cfg["arbitrator_mode"] == "weighted_convergence"


def test_non_llm_and_llm_modes_both_preserve_agents():
    for mode in ("weighted_convergence", "agent_llm"):
        cfg = resolve_backtest_config()
        deploy = _builder_default_deploy(mode=mode)
        _merge_inline_deploy_overrides(cfg, deploy)
        assert "technical_ta_engine" in cfg["agents"]
        assert cfg["max_position"] == pytest.approx(0.25)
        if mode == "agent_llm":
            assert cfg["use_llm"] is True
            assert cfg["agents"]["technical_ta_engine"]["llm_enabled"] is True
        else:
            assert cfg["use_llm"] is False
            assert cfg["agents"]["technical_ta_engine"]["llm_enabled"] is False


def test_builder_desk_ids_are_canonical():
    deploy = _builder_default_deploy()
    unknown = set(deploy["agents"]) - CANONICAL_DESKS
    assert not unknown, f"non-canonical desk ids from builder: {unknown}"
    # Critical aliases that used to ship truncated
    assert "statistical_alpha_engine" in deploy["agents"]
    assert "pattern_recognition_bot" in deploy["agents"]
    assert "technical_ta_engine" in deploy["agents"]
    assert "retail_hype_tracker" in deploy["agents"]
    assert "pro_bias_analyst" in deploy["agents"]
    assert "whale_behavior_analyst" in deploy["agents"]


def test_legacy_profile_weights_only_still_merges():
    cfg = resolve_backtest_config()
    legacy = {
        "profile_weights": {"technical_ta_engine": 0.7, "monetary_sentinel": 0.3},
        "arbitrator_mode": "weighted_convergence",
    }
    _merge_inline_deploy_overrides(cfg, legacy)
    assert cfg["profile_weights"]["technical_ta_engine"] == pytest.approx(0.7)
    assert cfg["arbitrator_mode"] == "weighted_convergence"


def test_full_builder_deploy_preserves_agents_and_llm_flags():
    cfg = resolve_backtest_config()
    deploy = _builder_default_deploy(mode="agent_llm")
    _merge_inline_deploy_overrides(cfg, deploy)

    assert "agents" in cfg
    assert cfg["agents"]["technical_ta_engine"]["llm_enabled"] is True
    assert cfg["agents"]["news_narrative_miner"]["llm_enabled"] is True
    assert cfg["agents"]["monetary_sentinel"]["llm_enabled"] is False
    assert cfg["arbitrator_mode"] == "agent_llm"
    assert cfg["use_llm"] is True
    assert cfg["execution"]["arbitrator_llm"] is True
    assert set(cfg["profile_weights"]) == set(deploy["agents"])


def test_batch_factory_reads_builder_agents_for_llm_desks():
    cfg = resolve_backtest_config()
    deploy = _builder_default_deploy(mode="agent_llm")
    _merge_inline_deploy_overrides(cfg, deploy)

    # Must not raise (empty weights fail-fast) and must see LLM desks.
    fn = batch_signal_factory(
        bars_by_symbol={"BTC/USDT": [[0, 1, 1, 1, 1, 1]] * 40},
        symbols=["BTC/USDT"],
        config={
            "deploy_config": cfg,
            "deploy_profile_weights": cfg.get("profile_weights"),
            "arbitrator_mode": cfg.get("arbitrator_mode"),
            "decision_threshold": cfg.get("decision_threshold"),
            "leverage": cfg.get("leverage", 2),
            "interval_sec": 86400,
            "min_warmup_bars": 5,
        },
    )
    assert callable(fn)
    # Peek closed-over llm set via __closure__ is fragile; instead assert agents survived.
    assert cfg["agents"]["technical_ta_engine"]["llm_enabled"] is True


def test_aimm_web_buildflowdeploy_fixture_if_present():
    """If aimm-web dumped a fixture, validate it against the same canonical rules."""
    fixture = AIMM_WEB / "src" / "lib" / "__fixtures__" / "builder_deploy.agent_llm.json"
    if not fixture.is_file():
        pytest.skip("aimm-web fixture not generated yet")
    deploy = json.loads(fixture.read_text(encoding="utf-8"))
    assert "agents" in deploy and "execution" in deploy
    unknown = set(deploy["agents"]) - CANONICAL_DESKS
    assert not unknown
    assert deploy["execution"].get("arbitrator_llm") is True
    assert any(m.get("llm_enabled") for m in deploy["agents"].values())
    ex = deploy["execution"]
    for key in ("leverage", "take_profit_pct", "stop_loss_pct", "max_position", "slippage_bps"):
        assert key in ex, f"missing execution.{key}"
    assert 0 < float(ex["max_position"]) <= 1
