"""Live Nexus Skills API provider (production / paper)."""

from __future__ import annotations

import logging
import time
from typing import Any

from nexus_data.client import NexusDataClient
from nexus_data.feeds import (
    fetch_nexus_global_bundle,
    fetch_nexus_per_symbol,
    merge_bundle_with_per_symbol,
    nexus_feeds_enabled,
)

logger = logging.getLogger(__name__)


class LiveNexusProvider:
    name = "live"

    def get_bundle(
        self,
        *,
        as_of_ms: int | None = None,
        universe: list[str] | None = None,
        market_data: dict[str, Any] | None = None,
        primary: str | None = None,
    ) -> dict[str, Any]:
        del as_of_ms, market_data, primary  # live always means "now"
        if not nexus_feeds_enabled():
            return {
                "fetched_at_epoch": time.time(),
                "source": "live_disabled",
                "endpoints": {},
                "errors": ["nexus_feeds_disabled"],
                "per_symbol": {"by_symbol": {}, "errors": []},
            }
        try:
            client = NexusDataClient()
            global_bundle = fetch_nexus_global_bundle(client)
        except Exception as e:
            logger.warning("Live Nexus global bundle failed: %s", e)
            return {
                "fetched_at_epoch": time.time(),
                "source": "live",
                "endpoints": {},
                "errors": [str(e)],
                "per_symbol": {"by_symbol": {}, "errors": []},
            }

        syms = [s for s in (universe or []) if isinstance(s, str)]
        if not syms:
            global_bundle["source"] = "live"
            return global_bundle
        try:
            per = fetch_nexus_per_symbol(client, syms)
            merged = merge_bundle_with_per_symbol(global_bundle, per)
            merged["source"] = "live"
            return merged
        except Exception as e:
            logger.warning("Live Nexus per-symbol failed: %s", e)
            out = dict(global_bundle)
            out["source"] = "live"
            out["errors"] = list(out.get("errors") or []) + [str(e)]
            return out


__all__ = ["LiveNexusProvider"]
