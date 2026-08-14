"""Agent registry + deploy-JSON agentic config."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agents.registry import get_registry


def test_registry_resolves_names_only():
    reg = get_registry()
    spec = reg.get("technical_ta_engine")
    assert spec is not None
    assert spec.name == "technical_ta_engine"
    assert spec.label == "Technical Analysis"
    assert reg.get("2.3") is None


def test_deploy_loader_agents_and_weights(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    deploy = {
        "agents": {
            "technical_ta_engine": {"weight": 0.6, "llm_enabled": True, "enabled": True},
            "pattern_recognition_bot": {"weight": 0.4, "llm_enabled": False},
            "statistical_alpha_engine": {"weight": 0.0, "enabled": False},
        },
        "execution": {"use_llm_synthesis": True, "desk_debate_llm": False, "arbitrator_llm": True},
    }
    path = tmp_path / "deploy.active.json"
    path.write_text(json.dumps(deploy), encoding="utf-8")
    monkeypatch.setenv("AIMM_DEPLOY_CONFIG_PATH", str(path))

    from config import deploy_loader

    agents = deploy_loader.get_deploy_agents()
    assert agents is not None
    assert "technical_ta_engine" in agents
    assert agents["technical_ta_engine"]["llm_enabled"] is True

    weights = deploy_loader.get_effective_weights()
    assert weights is not None
    assert weights.get("technical_ta_engine") == pytest.approx(0.6)
    assert weights.get("pattern_recognition_bot") == pytest.approx(0.4)
    assert "statistical_alpha_engine" not in weights

    assert deploy_loader.get_use_llm_synthesis() is True
    assert deploy_loader.get_desk_debate_llm() is False
    assert deploy_loader.get_arbitrator_mode() == "agent_llm"
    assert deploy_loader.get_arbitrator_llm() is True
    assert deploy_loader.get_llm_enabled_agent_names() == ["technical_ta_engine"]
    assert deploy_loader.get_enabled_agent_names() == [
        "technical_ta_engine",
        "pattern_recognition_bot",
    ]
    assert deploy_loader.resolve_tier0_graph_nodes() == [
        "pattern_recognition_bot",
        "technical_ta_engine",
    ]


def test_deploy_loader_drops_unknown_agent_keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    path = tmp_path / "deploy.active.json"
    path.write_text(
        json.dumps(
            {
                "agents": {
                    "technical_ta_engine": {"weight": 1.0, "enabled": True},
                    "not_a_real_agent": {"weight": 0.5, "enabled": True},
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AIMM_DEPLOY_CONFIG_PATH", str(path))
    from config import deploy_loader

    agents = deploy_loader.get_deploy_agents()
    assert agents is not None
    assert "not_a_real_agent" not in agents
    assert "technical_ta_engine" in agents


def test_resolve_agent_weights_does_not_merge_defaults():
    from workflow.weighted_arbitrator import _resolve_agent_weights

    w = _resolve_agent_weights(
        {"profile_weights": {"technical_ta_engine": 0.75, "pattern_recognition_bot": 0.25}}
    )
    assert set(w) == {"technical_ta_engine", "pattern_recognition_bot"}
    assert w["technical_ta_engine"] == pytest.approx(0.75)
    assert w["pattern_recognition_bot"] == pytest.approx(0.25)


def test_resolve_arbitrator_mode_defaults_to_weighted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.setenv("AIMM_DEPLOY_CONFIG_PATH", str(tmp_path / "missing.json"))
    from workflow.weighted_arbitrator import _resolve_arbitrator_mode

    assert _resolve_arbitrator_mode({}) == "weighted_convergence"


def test_get_effective_weights_ignores_legacy_field(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    path = tmp_path / "deploy.active.json"
    path.write_text(
        json.dumps(
            {
                "effective_weights": {"technical_ta_engine": 0.9},
                "execution": {"use_llm_synthesis": False},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AIMM_DEPLOY_CONFIG_PATH", str(path))
    from config import deploy_loader

    assert deploy_loader.get_effective_weights() is None


def test_omitted_overlay_flags_default_false(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    path = tmp_path / "deploy.active.json"
    path.write_text(json.dumps({"execution": {"use_llm_synthesis": True}}), encoding="utf-8")
    monkeypatch.setenv("AIMM_DEPLOY_CONFIG_PATH", str(path))
    from config import deploy_loader

    assert deploy_loader.get_arbitrator_llm() is False
