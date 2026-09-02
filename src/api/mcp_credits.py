"""Optional MCP credit check + deduct against aimm-web-api.

Gated by Flow env ``MCP_CREDITS_ENABLED`` (default off). When on, ``run_backtest``
reserves credits from the Profile nxk_ ledger (same Mongo as aimm-web) before enqueue.

Requires:
* ``WEB_API_URL`` — aimm-web-api origin (compose sets http://web-api:3002; not a frontend URL)
* ``AIMM_API_KEY`` — must equal web-api ``FLOW_API_KEY`` (sent as X-Flow-Key)
* caller ``X-API-KEY`` — the bound Profile nxk_ key
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from api.mcp_bindings import McpBinding

log = logging.getLogger("aimm.mcp.credits")
TIMEOUT_SEC = 8.0


class McpCreditsError(Exception):
    def __init__(
        self,
        status: int,
        error: str,
        detail: str = "",
        extra: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(detail or error)
        self.status = int(status)
        self.error = error
        self.detail = detail or error
        self.extra = extra or {}

    def as_http_detail(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "error": self.error,
            "hint": self.detail,
        }
        body.update(self.extra)
        return body


def mcp_credits_enabled() -> bool:
    raw = (os.getenv("MCP_CREDITS_ENABLED") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def web_api_base_url() -> str:
    raw = (os.getenv("WEB_API_URL") or "").strip().rstrip("/")
    if raw.endswith("/api"):
        raw = raw[: -len("/api")].rstrip("/")
    return raw


def _flow_service_key() -> str:
    return (os.getenv("AIMM_API_KEY") or "").strip()


def _post(path: str, binding: McpBinding, body: dict[str, Any]) -> dict[str, Any]:
    base = web_api_base_url()
    if not base:
        raise McpCreditsError(
            503,
            "credits_unconfigured",
            "MCP_CREDITS_ENABLED requires WEB_API_URL (aimm-web-api origin, not the aimm-web frontend).",
        )
    service_key = _flow_service_key()
    if not service_key:
        raise McpCreditsError(
            503,
            "credits_unconfigured",
            "MCP_CREDITS_ENABLED requires AIMM_API_KEY (must equal web-api FLOW_API_KEY).",
        )
    api_key = (binding.api_key or "").strip()
    if not api_key:
        raise McpCreditsError(401, "unknown_api_key", "MCP binding has no API key")
    url = f"{base}{path}"
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-Flow-Key": service_key,
        "X-API-KEY": api_key,
    }
    try:
        with httpx.Client(timeout=TIMEOUT_SEC) as client:
            res = client.post(url, headers=headers, json=body)
    except httpx.HTTPError as exc:
        raise McpCreditsError(
            503,
            "credits_unreachable",
            f"aimm-web-api unreachable at {base}: {exc}",
        ) from exc

    payload: dict[str, Any] = {}
    try:
        parsed = res.json()
        if isinstance(parsed, dict):
            payload = parsed
    except ValueError:
        payload = {"detail": (res.text or "")[:400]}

    if res.status_code >= 400:
        error = str(payload.get("error") or payload.get("detail") or f"HTTP {res.status_code}")
        if isinstance(payload.get("detail"), dict):
            nested = payload["detail"]
            error = str(nested.get("error") or error)
            payload = {**payload, **nested}
        hint = str(payload.get("detail") or payload.get("hint") or error)
        extra = {k: payload[k] for k in ("required", "available", "reservation_id") if k in payload}
        status = res.status_code
        if status == 401 and error in {"unauthorized", "flow_key_unconfigured"}:
            status = 503
            error = "credits_unconfigured"
            hint = "Flow AIMM_API_KEY must equal aimm-web-api FLOW_API_KEY."
        raise McpCreditsError(status, str(error), hint, extra)
    return payload


def reserve_mcp_credits(binding: McpBinding, *, n_bars: int) -> dict[str, Any] | None:
    """Reserve aimm-web credits for this MCP key. None when the env gate is off."""
    if not mcp_credits_enabled():
        return None
    payload = _post(
        "/api/internal/mcp/credits/reserve",
        binding,
        {"strategy_id": binding.strategy_id, "n_bars": n_bars},
    )
    reservation_id = str(payload.get("reservation_id") or "").strip()
    if not reservation_id:
        raise McpCreditsError(
            502,
            "credits_bad_response",
            "aimm-web-api reserve did not return reservation_id",
        )
    return payload


def refund_mcp_credits(binding: McpBinding, reservation: dict[str, Any]) -> None:
    rid = str(reservation.get("reservation_id") or "").strip()
    if not rid:
        return
    if int(reservation.get("credits_reserved") or 0) <= 0:
        return
    try:
        _post(
            "/api/internal/mcp/credits/refund",
            binding,
            {"reservation_id": rid},
        )
    except McpCreditsError as exc:
        log.warning("MCP credit refund failed reservation=%s: %s", rid, exc.detail)


def attach_mcp_flow_run(
    binding: McpBinding,
    reservation: dict[str, Any],
    flow_run_id: str,
) -> None:
    rid = str(reservation.get("reservation_id") or "").strip()
    frid = (flow_run_id or "").strip()
    if not rid or not frid:
        return
    try:
        _post(
            "/api/internal/mcp/credits/attach",
            binding,
            {"reservation_id": rid, "flow_run_id": frid},
        )
    except McpCreditsError as exc:
        log.warning("MCP credit attach failed reservation=%s: %s", rid, exc.detail)
