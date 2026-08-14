"""Config Designer validation (outside-graph)."""

from agents.config_designer import ConfigDesignerAgent, validate_deploy_draft


def test_unknown_agent_key_warns():
    warnings = validate_deploy_draft(
        {"agents": {"not_a_real_agent": {"weight": 1.0, "enabled": True}}}
    )
    assert any("Unknown agent key: not_a_real_agent" in w for w in warnings)


def test_seed_macro_tilt_is_valid():
    result = ConfigDesignerAgent().seed_style("macro_tilt")
    assert result.draft_config is not None
    assert "technical_ta_engine" in result.draft_config["agents"]
    assert result.draft_config["execution"]["arbitrator_llm"] is True
    assert result.warnings == []
