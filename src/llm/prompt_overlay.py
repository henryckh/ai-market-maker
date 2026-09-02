"""Shared operator prompt overlay merge (arbitrator + desk LLM)."""

from __future__ import annotations

from config.agent_prompts import AgentPromptSettings


def merge_operator_prompt(engineered: str, ps: AgentPromptSettings | None) -> str:
    """Keep engineered rules; append operator JSON as overlay."""
    system = engineered
    if ps is None:
        return system
    if ps.system_prompt.strip():
        system = system.rstrip() + "\n\nOperator policy:\n" + ps.system_prompt.strip()
    if ps.task_prompt.strip():
        system = system.rstrip() + "\n\nOperator task prompt:\n" + ps.task_prompt.strip()
    return system
