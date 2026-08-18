"""Nexus context provider protocol — live and historical share one contract.

Agents only consume ``shared_memory["nexus"]`` bundles. Providers never leak into
Tier-0 agent code.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class NexusContextProvider(Protocol):
    """Resolve a Nexus-shaped bundle for the graph (live or as-of historical)."""

    name: str

    def get_bundle(
        self,
        *,
        as_of_ms: int | None = None,
        universe: list[str] | None = None,
        market_data: dict[str, Any] | None = None,
        primary: str | None = None,
    ) -> dict[str, Any]:
        """Return endpoints / per_symbol / errors shape used by Tier-0 agents."""
        ...


def resolve_nexus_provider(*, run_mode: str) -> NexusContextProvider:
    """Factory: backtest → historical; otherwise live."""
    mode = (run_mode or "").strip().lower()
    if mode in ("backtest", "bt", "historical"):
        from nexus_data.historical.provider import HistoricalNexusProvider

        return HistoricalNexusProvider()
    from nexus_data.live_provider import LiveNexusProvider

    return LiveNexusProvider()


__all__ = ["NexusContextProvider", "resolve_nexus_provider"]
