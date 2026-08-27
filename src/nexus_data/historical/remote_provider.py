"""Remote historical Nexus provider — queries the datalayer-api historical snapshot store.

Replaces CSV-backed HistoricalNexusProvider. Same contract, HTTP data source.

Usage:
    provider = RemoteNexusProvider(base_url="http://localhost:3001")
    bundle = provider.get_bundle(as_of_ms=1710806400000, universe=["BTC/USDT", "ETH/USDT"])
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

try:
    import requests
except ImportError:
    requests = None  # type: ignore[assignment]

from nexus_data.historical.store import ms_to_utc_date

logger = logging.getLogger(__name__)


class RemoteNexusProvider:
    """Build nexus bundles from datalayer-api /api/v1/historical/nexus endpoint.

    Same contract as HistoricalNexusProvider — drop-in replacement.
    """

    name = "remote_historical"

    def __init__(self, *, base_url: str | None = None):
        self._base_url = base_url or os.getenv("DATALAYER_API_URL", "http://localhost:3001")
        # Internal secret for snapshot triggering (not needed for reads)
        self._internal_secret = os.getenv("DATALAYER_INTERNAL_SECRET") or None
        self._timeout_s = float(os.getenv("DATALAYER_TIMEOUT_S") or "15")

    def _request(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if requests is None:
            return {
                "error": "requests_not_installed",
                "endpoints": {},
                "per_symbol": {"by_symbol": {}, "errors": []},
            }

        url = f"{self._base_url}{path}"
        try:
            resp = requests.get(url, params=params, timeout=self._timeout_s)
            resp.raise_for_status()
            body = resp.json()
            if not isinstance(body, dict):
                return {
                    "error": "invalid_response",
                    "endpoints": {},
                    "per_symbol": {"by_symbol": {}, "errors": []},
                }
            data = body.get("data")
            if isinstance(data, dict):
                return data
            return {
                "error": "no_data",
                "endpoints": {},
                "per_symbol": {"by_symbol": {}, "errors": []},
            }
        except Exception as e:
            logger.warning("RemoteNexusProvider: %s failed: %s", path, e)
            return {"error": str(e), "endpoints": {}, "per_symbol": {"by_symbol": {}, "errors": []}}

    def get_bundle(
        self,
        *,
        as_of_ms: int | None = None,
        universe: list[str] | None = None,
        market_data: dict[str, Any] | None = None,
        primary: str | None = None,
    ) -> dict[str, Any]:
        """Fetch nexus bundle from datalayer-api historical snapshots."""
        ts = int(as_of_ms) if as_of_ms is not None else int(time.time() * 1000)
        day = ms_to_utc_date(ts)

        params: dict[str, Any] = {"as_of": day}
        primary_sym = primary or (universe[0] if universe else "BTC/USDT")
        params["primary"] = primary_sym
        if universe:
            params["universe"] = ",".join(universe)

        data = self._request("/api/v1/historical/nexus", params=params)

        if data.get("error"):
            # Fallback: return empty bundle so desks degrade gracefully
            return {
                "fetched_at_epoch": time.time(),
                "as_of_ms": ts,
                "as_of_date": day,
                "source": "remote_historical_fallback",
                "endpoints": {},
                "per_symbol": {"by_symbol": {}, "errors": [str(data.get("error"))]},
                "errors": [f"remote_historical: {data.get('error')}"],
            }

        data.setdefault("source", "datalayer:historical")
        data.setdefault("as_of_ms", ts)
        data.setdefault("as_of_date", day)
        data.setdefault("fetched_at_epoch", time.time())

        return data

    def trigger_snapshot(
        self, date: str | None = None, universe: list[str] | None = None
    ) -> dict[str, Any]:
        """Trigger a new snapshot via internal API (requires DATALAYER_INTERNAL_SECRET)."""
        if not self._internal_secret:
            return {"error": "DATALAYER_INTERNAL_SECRET not set"}

        url = f"{self._base_url}/api/internal/historical/snapshot"
        payload: dict[str, Any] = {}
        if date:
            payload["date"] = date
        if universe:
            payload["universe"] = universe

        try:
            resp = requests.post(
                url,
                json=payload,
                headers={"x-internal-secret": self._internal_secret},
                timeout=60.0,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error("RemoteNexusProvider: trigger_snapshot failed: %s", e)
            return {"error": str(e)}


__all__ = ["RemoteNexusProvider"]
