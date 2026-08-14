"""Agent LLM toggles — deploy JSON is the source of truth."""

from __future__ import annotations

from typing import Mapping

_AGENT_LLM_MODE = "agent_llm"
_WEIGHTED_CONVERGENCE = "weighted_convergence"


def current_run_mode(env: Mapping[str, str] | None = None) -> str:
    """Return ``agent_llm`` or ``weighted_convergence`` from deploy config."""
    try:
        from config.deploy_loader import get_use_llm_synthesis

        use_llm = get_use_llm_synthesis()
        if use_llm is True:
            return _AGENT_LLM_MODE
        if use_llm is False:
            return _WEIGHTED_CONVERGENCE
    except Exception:
        pass
    return _WEIGHTED_CONVERGENCE


def is_agent_llm_mode(env: Mapping[str, str] | None = None) -> bool:
    return current_run_mode(env) == _AGENT_LLM_MODE


def agent_llm_enabled(
    agent_id: str,
    env: Mapping[str, str] | None = None,
    *,
    default: bool = False,
) -> bool:
    """Whether *agent_id* may use the LLM path (from deploy ``agents.*.llm_enabled``)."""
    try:
        from config.deploy_loader import get_llm_enabled_agent_names

        names = get_llm_enabled_agent_names()
        if names is not None:
            return agent_id in names
    except Exception:
        pass
    return default
