"""Canonical Tier-0 agent registry. Keys are readable snake_case names only."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Iterable


@dataclass(frozen=True)
class AgentSpec:
    name: str
    label: str
    default_weight: float = 0.0
    default_llm_enabled: bool = False


_SPECS: tuple[AgentSpec, ...] = (
    AgentSpec("monetary_sentinel", "Macro Economist", 0.25),
    AgentSpec("news_narrative_miner", "News & Narrative", 0.05),
    AgentSpec("pattern_recognition_bot", "Pattern Recognition", 0.15),
    AgentSpec("statistical_alpha_engine", "Statistical Alpha", 0.10),
    AgentSpec("technical_ta_engine", "Technical Analysis", 0.55),
    AgentSpec("retail_hype_tracker", "Retail Hype Tracker", 0.05),
    AgentSpec("pro_bias_analyst", "Smart Money Tracker", 0.05),
    AgentSpec("whale_behavior_analyst", "Whale Behavior", 0.0),
    AgentSpec("liquidity_order_flow", "Liquidity & Order Flow", 0.15),
)


class AgentRegistry:
    def __init__(self, specs: Iterable[AgentSpec] = _SPECS) -> None:
        self._by_name = {s.name: s for s in specs}

    def get(self, name: str) -> AgentSpec | None:
        return self._by_name.get((name or "").strip())

    def require(self, name: str) -> AgentSpec:
        spec = self.get(name)
        if spec is None:
            raise KeyError(f"unknown agent: {name!r}")
        return spec

    def all_specs(self) -> list[AgentSpec]:
        return list(self._by_name.values())

    def names(self) -> list[str]:
        return list(self._by_name.keys())

    def default_weights(self) -> dict[str, float]:
        return {s.name: s.default_weight for s in self._by_name.values()}

    def labels(self) -> dict[str, str]:
        return {s.name: s.label for s in self._by_name.values()}


@lru_cache(maxsize=1)
def get_registry() -> AgentRegistry:
    return AgentRegistry()


__all__ = ["AgentSpec", "AgentRegistry", "get_registry"]
