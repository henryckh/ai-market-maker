"""Inline agent_prompts overlay from Strategy Builder deploy."""

from __future__ import annotations

from api.backtest_routes import _merge_inline_deploy_overrides
from config.agent_prompts import (
    AgentPromptSettings,
    apply_inline_prompt_overrides,
    clear_inline_prompt_overrides,
    prompt_settings_by_actor,
)
from llm.agent_llm_client import _build_agent_prompt
from llm.prompt_overlay import merge_operator_prompt


def test_prompt_settings_by_actor_inline_overrides_file() -> None:
    token = apply_inline_prompt_overrides(
        [
            AgentPromptSettings(
                node_id="signal_arbitrator",
                actor_id="signal_arbitrator",
                system_prompt="HACKATHON_OVERLAY_MARKER",
                task_prompt="",
                cot_enabled=True,
            )
        ]
    )
    try:
        ps = prompt_settings_by_actor().get("signal_arbitrator")
        assert ps is not None
        assert "HACKATHON_OVERLAY_MARKER" in ps.system_prompt
    finally:
        clear_inline_prompt_overrides(token)


def test_merge_inline_deploy_carries_agent_prompts() -> None:
    cfg: dict = {}
    deploy = {
        "agent_prompts": [
            {
                "node_id": "technical_ta_engine",
                "actor_id": "technical_ta_engine",
                "system_prompt": "Always emphasize momentum.",
                "task_prompt": "",
                "cot_enabled": True,
            }
        ]
    }
    _merge_inline_deploy_overrides(cfg, deploy)
    assert isinstance(cfg.get("agent_prompts"), list)
    assert cfg["agent_prompts"][0]["actor_id"] == "technical_ta_engine"


def test_desk_prompt_overlay_appends_operator_policy() -> None:
    token = apply_inline_prompt_overrides(
        [
            AgentPromptSettings(
                node_id="technical_ta_engine",
                actor_id="technical_ta_engine",
                system_prompt="Prefer breakout entries only.",
                task_prompt="",
                cot_enabled=True,
            )
        ]
    )
    try:
        system, _user = _build_agent_prompt(
            "technical_ta_engine",
            persona="Base persona",
            skill="Base skill",
            market_context="Ticker: BTC/USDT",
        )
        assert "Operator policy:" in system
        assert "Prefer breakout entries only." in system
        assert "Base persona" in system
    finally:
        clear_inline_prompt_overrides(token)


def test_arbitrator_merge_still_appends_overlay() -> None:
    engineered = "Core engineered rules."
    ps = AgentPromptSettings(
        node_id="signal_arbitrator",
        actor_id="signal_arbitrator",
        system_prompt="Favor mean reversion in chop.",
        task_prompt="",
        cot_enabled=True,
    )
    merged = merge_operator_prompt(engineered, ps)
    assert "Core engineered rules." in merged
    assert "Operator policy:" in merged
    assert "Favor mean reversion in chop." in merged
