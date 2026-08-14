"""Load active deploy config from config/deploy.active.json."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_DEPLOY_PATH = "config/deploy.active.json"


def _deploy_path() -> Path:
    override = (os.getenv("AIMM_DEPLOY_CONFIG_PATH") or "").strip()
    return Path(override) if override else Path(_DEFAULT_DEPLOY_PATH)


def load_deploy_config() -> dict[str, Any] | None:
    path = _deploy_path()
    if not path.is_file():
        return None
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        return obj if isinstance(obj, dict) else None
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("failed to read deploy config at %s: %s", path, e)
        return None


def get_deploy_agents() -> dict[str, dict[str, Any]] | None:
    cfg = load_deploy_config()
    if cfg is None:
        return None
    agents = cfg.get("agents")
    if not isinstance(agents, dict) or not agents:
        return None
    from agents.registry import get_registry

    known = set(get_registry().names())
    out: dict[str, dict[str, Any]] = {}
    for key, meta in agents.items():
        name = str(key).strip()
        if name not in known:
            logger.warning("deploy agents: unknown key %r ignored", name)
            continue
        out[name] = dict(meta) if isinstance(meta, dict) else {}
    return out or None


def get_effective_weights() -> dict[str, float] | None:
    """Weights keyed by agent name."""
    agents = get_deploy_agents()
    if agents:
        weights: dict[str, float] = {}
        for name, meta in agents.items():
            if meta.get("enabled", True) is False:
                continue
            w = meta.get("weight")
            if w is None:
                continue
            try:
                weights[name] = float(w)
            except (TypeError, ValueError):
                continue
        return weights or None
    return None


def get_enabled_agent_names() -> list[str] | None:
    """Enabled desks from deploy JSON. ``None`` means no agents block (use registry)."""
    agents = get_deploy_agents()
    if agents is None:
        return None
    return [name for name, meta in agents.items() if meta.get("enabled", True) is not False]


def resolve_tier0_graph_nodes() -> list[str]:
    """LangGraph perception nodes: enabled deploy desks, else the full registry."""
    from agents.registry import get_registry

    known = get_registry().names()
    enabled = get_enabled_agent_names()
    if enabled is None:
        return known
    ordered = [n for n in known if n in enabled]
    if not ordered:
        logger.warning("deploy JSON has no enabled agents; using full registry")
        return known
    return ordered


def get_llm_enabled_agent_names() -> list[str] | None:
    agents = get_deploy_agents()
    if agents is None:
        return None
    return [
        name
        for name, meta in agents.items()
        if meta.get("llm_enabled") and meta.get("enabled", True) is not False
    ]


def get_use_llm_synthesis() -> bool | None:
    cfg = load_deploy_config()
    if cfg is None:
        return None
    execution = cfg.get("execution") if isinstance(cfg.get("execution"), dict) else {}
    if "use_llm_synthesis" in execution:
        return bool(execution.get("use_llm_synthesis"))
    mode = str(execution.get("arbitrator_mode") or "").strip().lower()
    if mode in ("agent_llm", "llm", "full_agentic"):
        return True
    if mode in ("weighted_convergence", "weighted", "measurement", "none"):
        return False
    return None


def get_desk_debate_llm() -> bool | None:
    cfg = load_deploy_config()
    if cfg is None:
        return None
    execution = cfg.get("execution") if isinstance(cfg.get("execution"), dict) else {}
    if "desk_debate_llm" in execution:
        return bool(execution.get("desk_debate_llm"))
    if "desk_debate_enabled" in execution:
        return bool(execution.get("desk_debate_enabled"))
    return None


def _execution_flag(name: str) -> bool | None:
    cfg = load_deploy_config()
    if cfg is None:
        return None
    execution = cfg.get("execution") if isinstance(cfg.get("execution"), dict) else {}
    if name not in execution:
        return None
    return bool(execution.get(name))


def get_arbitrator_llm() -> bool:
    """LLM overlay on the weighted decision. Default off."""
    return bool(_execution_flag("arbitrator_llm"))


def get_arbitrator_mode() -> str | None:
    """How desks feed the weighted formula — not a per-bar reweight.

    Weights stay as configured in deploy JSON. ``agent_llm`` only means
    ``llm_enabled`` desks may call the model before the same weighted math.
    """
    use_llm = get_use_llm_synthesis()
    if use_llm is True:
        return "agent_llm"
    if use_llm is False:
        return "weighted_convergence"
    cfg = load_deploy_config()
    if cfg is None:
        return None
    execution = cfg.get("execution") if isinstance(cfg.get("execution"), dict) else {}
    mode = execution.get("arbitrator_mode")
    return str(mode) if mode else None


def get_deploy_policy() -> dict[str, Any] | None:
    cfg = load_deploy_config()
    return None if cfg is None else cfg.get("policy")


def get_deploy_profile_id() -> str | None:
    cfg = load_deploy_config()
    if cfg is None:
        return None
    return cfg.get("profile", {}).get("profile_id")


def get_decision_threshold() -> dict[str, Any] | None:
    cfg = load_deploy_config()
    if cfg is None:
        return None
    thr = cfg.get("decision_threshold")
    return dict(thr) if isinstance(thr, dict) else None


__all__ = [
    "load_deploy_config",
    "get_effective_weights",
    "get_arbitrator_mode",
    "get_use_llm_synthesis",
    "get_desk_debate_llm",
    "get_llm_enabled_agent_names",
    "get_deploy_policy",
    "get_deploy_agents",
    "get_enabled_agent_names",
    "resolve_tier0_graph_nodes",
    "get_deploy_profile_id",
    "get_decision_threshold",
    "get_arbitrator_llm",
]
