"""Nexus context provider protocol — live and historical share one contract.

Agents only consume ``shared_memory["nexus"]`` bundles. Providers never leak into
Tier-0 agent code.
"""

from __future__ import annotations

import os
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
    """Factory: backtest → remote_historical (preferred) or CSV historical; otherwise live."""
    mode = (run_mode or "").strip().lower()
    if mode in ("backtest", "bt", "historical"):
        # Prefer remote historical from datalayer-api if URL is configured
        remote_url = os.getenv("DATALAYER_API_URL") or ""
        prefer_remote = remote_url.strip() and os.getenv(
            "NEXUS_PROVIDER_MODE", "remote"
        ).strip().lower() in ("remote", "auto")
        if prefer_remote:
            try:
                from nexus_data.historical.remote_provider import RemoteNexusProvider

                logger = __import__("logging").getLogger(__name__)
                logger.info("Using RemoteNexusProvider (datalayer-api: %s)", remote_url)
                return RemoteNexusProvider(base_url=remote_url)
            except ImportError:
                pass

        from nexus_data.historical.provider import HistoricalNexusProvider

        return HistoricalNexusProvider()
    from nexus_data.live_provider import LiveNexusProvider

    return LiveNexusProvider()


__all__ = ["NexusContextProvider", "resolve_nexus_provider"]
