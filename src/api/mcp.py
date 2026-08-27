"""OlaXBT Nexus Strategy API HTTP surface (olaxbt-mcp/1.0).

Endpoints (user-level ``X-API-KEY``, not the global ``AIMM_API_KEY``):

* ``GET  /mcp/tools`` — tools manifest
* ``POST /mcp/tools/call`` — invoke a tool
* ``GET  /mcp/health`` — liveness (no auth)
* ``POST /mcp/admin/bind`` — map Profile nxk_ key → strategy_id (ops, AIMM_API_KEY)
* ``POST /mcp/admin/unbind`` — drop a key mapping (ops)
* ``POST /mcp/admin/publish`` — refresh caches from a backtest run (ops or bound user key)

Strategy tools read cached JSON (no LangGraph). Market-data tools proxy a small Datalayer read set.
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

from api.control_plane_secrets import is_usable_secret, presented_matches
from api.mcp_bindings import (
    delete_binding,
    hash_api_key,
    resolve_binding,
    set_run_id_for_strategy,
    upsert_binding,
)
from api.mcp_cache import (
    get_strategy_equity,
    get_strategy_metrics,
    get_strategy_signal,
    get_strategy_trades,
    publish_from_run,
    publish_strategy_run,
)
from api.mcp_credits import McpCreditsError, mcp_credits_enabled
from api.mcp_datalayer import (
    get_etf_flow,
    get_fear_greed,
    get_historical_coverage,
    get_historical_funding,
    get_historical_ohlcv,
    get_macro,
    get_market_snapshot,
    get_news,
    get_oi_ranking,
    get_open_interest,
    get_sentiment,
    get_vcp,
)
from api.mcp_jobs import get_job_for_binding, run_backtest_for_binding
from api.safe_ids import require_safe_id

router = APIRouter(prefix="/mcp", tags=["mcp"])


def _tool(
    name: str,
    description: str,
    schema: dict[str, Any],
    *,
    category: str,
) -> dict[str, Any]:
    return {
        "name": name,
        "category": category,
        "description": description,
        "inputSchema": schema,
    }


_EMPTY = {"type": "object", "properties": {}, "additionalProperties": False}

_DATE = {"type": "string", "description": "UTC date YYYY-MM-DD"}
_SYM = {"type": "string", "description": "e.g. BTC/USDT"}
_AS_OF_SCHEMA = {
    "type": "object",
    "properties": {"as_of": _DATE, "symbol": _SYM},
    "required": ["as_of"],
    "additionalProperties": False,
}

TOOLS_MANIFEST: list[dict[str, Any]] = [
    _tool(
        "get_strategy_signal",
        "Latest copy-trade action: BUY, SELL, or HOLD. Poll this.",
        {
            "type": "object",
            "properties": {"symbol": _SYM},
            "required": ["symbol"],
            "additionalProperties": False,
        },
        category="trading",
    ),
    _tool(
        "get_strategy_metrics",
        "Scorecard: Sharpe, return, win rate, drawdown, AUM, qualification.",
        _EMPTY,
        category="trading",
    ),
    _tool(
        "get_strategy_equity",
        "Equity curve for a performance chart.",
        _EMPTY,
        category="trading",
    ),
    _tool(
        "get_strategy_trades",
        "Recent filled trades (price, size, pnl).",
        _EMPTY,
        category="trading",
    ),
    _tool(
        "run_backtest",
        "Start an async backtest for the strategy bound to this key. Returns run_id to poll. "
        "When credits are enabled on the engine, the Profile nxk_ key's aimm-web balance is checked and reserved first.",
        {
            "type": "object",
            "properties": {
                "n_bars": {
                    "type": "integer",
                    "description": "Eval bars (20–500). Default: last Studio run.",
                }
            },
            "additionalProperties": False,
        },
        category="trading",
    ),
    _tool(
        "get_backtest_job",
        "Poll backtest progress by run_id (or latest job for this strategy).",
        {
            "type": "object",
            "properties": {
                "run_id": {"type": "string", "description": "From run_backtest. Omit for latest."}
            },
            "additionalProperties": False,
        },
        category="trading",
    ),
    _tool(
        "get_historical_ohlcv",
        "Daily candles as of a date, plus last funding and RSI/MA.",
        {
            "type": "object",
            "properties": {"as_of": _DATE, "symbol": _SYM, "limit": {"type": "integer"}},
            "required": ["as_of"],
            "additionalProperties": False,
        },
        category="historical",
    ),
    _tool(
        "get_historical_funding",
        "Perpetual last funding rate on a UTC date.",
        _AS_OF_SCHEMA,
        category="historical",
    ),
    _tool(
        "get_open_interest",
        "Open interest and long/short ratio as of a UTC date.",
        _AS_OF_SCHEMA,
        category="historical",
    ),
    _tool(
        "get_vcp",
        "Minervini/VCP setup gates as of a UTC date.",
        _AS_OF_SCHEMA,
        category="historical",
    ),
    _tool(
        "get_macro",
        "FRED/macro overlay from the daily snapshot (VIX, 10Y, Fed funds, DXY, TVL).",
        {
            "type": "object",
            "properties": {"as_of": _DATE},
            "required": ["as_of"],
            "additionalProperties": False,
        },
        category="historical",
    ),
    _tool(
        "get_market_snapshot",
        "Fuller point-in-time market bundle for a UTC date.",
        {
            "type": "object",
            "properties": {"as_of": _DATE, "symbols": {"type": "string"}},
            "required": ["as_of"],
            "additionalProperties": False,
        },
        category="historical",
    ),
    _tool(
        "get_historical_coverage",
        "Available historical snapshot date range.",
        _EMPTY,
        category="historical",
    ),
    _tool(
        "get_etf_flow",
        "BTC/ETH ETF inflow series (latest points).",
        {
            "type": "object",
            "properties": {"limit": {"type": "integer"}},
            "additionalProperties": False,
        },
        category="historical",
    ),
    _tool(
        "get_oi_ranking",
        "Top open-interest ranking (live Datalayer).",
        {
            "type": "object",
            "properties": {"limit": {"type": "integer"}},
            "additionalProperties": False,
        },
        category="historical",
    ),
    _tool(
        "get_fear_greed",
        "Fear & Greed index from historical snapshots.",
        {"type": "object", "properties": {"as_of": _DATE}, "additionalProperties": False},
        category="historical",
    ),
    _tool(
        "get_news",
        "Recent aggregated crypto headlines (max 10).",
        {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "additionalProperties": False,
        },
        category="historical",
    ),
    _tool(
        "get_sentiment",
        "Market sentiment index (optional symbol).",
        {
            "type": "object",
            "properties": {"symbol": {"type": "string"}},
            "additionalProperties": False,
        },
        category="historical",
    ),
]

_DATALAYER_HANDLERS = {
    "get_historical_ohlcv": get_historical_ohlcv,
    "get_historical_funding": get_historical_funding,
    "get_open_interest": get_open_interest,
    "get_vcp": get_vcp,
    "get_macro": get_macro,
    "get_market_snapshot": get_market_snapshot,
    "get_historical_coverage": get_historical_coverage,
    "get_etf_flow": get_etf_flow,
    "get_oi_ranking": get_oi_ranking,
    "get_fear_greed": get_fear_greed,
    "get_news": get_news,
    "get_sentiment": get_sentiment,
}


def _extract_user_api_key(
    request: Request,
    x_api_key: str | None = None,
) -> str:
    key = (x_api_key or request.headers.get("x-api-key") or "").strip()
    if key:
        return key
    auth = (request.headers.get("authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        return auth.split(" ", 1)[1].strip()
    return ""


def _require_binding(request: Request, x_api_key: str | None = None):
    key = _extract_user_api_key(request, x_api_key)
    if not key:
        raise HTTPException(
            status_code=401,
            detail={
                "error": "unauthorized",
                "hint": "Set X-API-KEY to a strategy-bound MCP key",
            },
        )
    binding = resolve_binding(key)
    if binding is None:
        raise HTTPException(
            status_code=401,
            detail={
                "error": "unknown_api_key",
                "hint": "Register the key in MCP_API_KEYS_JSON or .runs/mcp/api_keys.json",
            },
        )
    return binding


class ToolCallRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    arguments: dict[str, Any] = Field(default_factory=dict)


class PublishRequest(BaseModel):
    run_id: str | None = Field(None, max_length=160)
    strategy_id: str | None = Field(None, max_length=128)
    symbol: str = Field("BTC/USDT", max_length=40)
    estimated_aum_usdt: float | None = Field(None, ge=0)


class BindRequest(BaseModel):
    api_key: str = Field(..., min_length=12, max_length=256)
    strategy_id: str = Field(..., min_length=1, max_length=128)
    user_id: str = Field("", max_length=80)
    label: str = Field("", max_length=80)
    run_id: str = Field("", max_length=160)
    estimated_aum_usdt: float | None = Field(None, ge=0)


class UnbindRequest(BaseModel):
    api_key: str | None = Field(None, max_length=256)
    key_hash: str | None = Field(None, max_length=64)


def _is_ops_key(request: Request, x_api_key: str | None) -> bool:
    presented = _extract_user_api_key(request, x_api_key)
    expected = (os.getenv("AIMM_API_KEY") or "").strip()
    if not is_usable_secret(expected):
        return False
    return presented_matches(presented, expected)


def _require_ops(request: Request, x_api_key: str | None) -> None:
    if not _is_ops_key(request, x_api_key):
        raise HTTPException(
            status_code=403,
            detail={
                "error": "ops_forbidden",
                "hint": "MCP admin bind/unbind/publish-by-strategy require AIMM_API_KEY",
            },
        )


@router.get("/health")
def mcp_health() -> dict[str, Any]:
    return {
        "ok": True,
        "service": "olaxbt-nexus-mcp",
        "tools": len(TOOLS_MANIFEST),
        "credits": mcp_credits_enabled(),
    }


@router.get("/docs")
def mcp_docs_redirect():
    """Public docs are hosted on the product gateway (aimm-web-api), same pattern as Datalayer /docs."""
    from fastapi.responses import RedirectResponse

    target = (
        os.getenv("MCP_PUBLIC_DOCS_URL") or ""
    ).strip() or "https://nexus.olaxbt.xyz/api/mcp/docs"
    return RedirectResponse(url=target, status_code=302)


@router.get("/tools")
def list_tools(
    request: Request,
    x_api_key: str | None = Header(default=None, alias="X-API-KEY"),
) -> dict[str, Any]:
    binding = _require_binding(request, x_api_key)
    return {
        "protocol": "olaxbt-mcp/1.0",
        "strategy_id": binding.strategy_id,
        "tools": TOOLS_MANIFEST,
    }


def _call_tool(name: str, arguments: dict[str, Any], binding) -> Any:
    tool = (name or "").strip()
    args = arguments if isinstance(arguments, dict) else {}

    if tool == "get_strategy_metrics":
        try:
            return get_strategy_metrics(binding)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    if tool == "get_strategy_signal":
        symbol = str(args.get("symbol") or "").strip()
        if not symbol:
            raise HTTPException(status_code=400, detail="symbol is required")
        try:
            return get_strategy_signal(binding, symbol=symbol)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    if tool == "get_strategy_equity":
        try:
            return get_strategy_equity(binding)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    if tool == "get_strategy_trades":
        try:
            return get_strategy_trades(binding)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    if tool == "run_backtest":
        try:
            return run_backtest_for_binding(binding, args)
        except McpCreditsError as exc:
            raise HTTPException(status_code=exc.status, detail=exc.as_http_detail()) from exc
        except HTTPException:
            raise
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    if tool == "get_backtest_job":
        try:
            return get_job_for_binding(binding, args)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    handler = _DATALAYER_HANDLERS.get(tool)
    if handler is not None:
        try:
            return handler(args, api_key=binding.api_key)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    raise HTTPException(status_code=404, detail=f"unknown tool: {tool}")


@router.post("/tools/call")
def call_tool(
    request: Request,
    body: ToolCallRequest,
    x_api_key: str | None = Header(default=None, alias="X-API-KEY"),
) -> dict[str, Any]:
    binding = _require_binding(request, x_api_key)
    result = _call_tool(body.name, body.arguments or {}, binding)
    return {
        "ok": True,
        "name": body.name,
        "strategy_id": binding.strategy_id,
        "content": result,
    }


@router.post("/admin/bind")
def admin_bind(
    request: Request,
    body: BindRequest,
    x_api_key: str | None = Header(default=None, alias="X-API-KEY"),
) -> dict[str, Any]:
    """Map a Profile nxk_ key onto a Studio strategy_id (ops — AIMM_API_KEY)."""
    _require_ops(request, x_api_key)
    sid = require_safe_id(body.strategy_id, name="strategy_id")
    try:
        binding = upsert_binding(
            body.api_key,
            strategy_id=sid,
            user_id=body.user_id,
            label=body.label,
            run_id=body.run_id,
            estimated_aum_usdt=body.estimated_aum_usdt,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    prefix = body.api_key[:12]
    return {
        "ok": True,
        "strategy_id": binding.strategy_id,
        "key_prefix": prefix,
        "key_hash": hash_api_key(body.api_key),
        "user_id": binding.user_id or None,
    }


@router.post("/admin/unbind")
def admin_unbind(
    request: Request,
    body: UnbindRequest,
    x_api_key: str | None = Header(default=None, alias="X-API-KEY"),
) -> dict[str, Any]:
    """Drop a Profile key mapping (ops). Accepts plaintext or SHA-256 hex."""
    _require_ops(request, x_api_key)
    try:
        removed = delete_binding(api_key=body.api_key, key_hash=body.key_hash)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "removed": removed}


@router.post("/admin/publish")
def admin_publish(
    request: Request,
    body: PublishRequest,
    x_api_key: str | None = Header(default=None, alias="X-API-KEY"),
) -> dict[str, Any]:
    """Refresh metrics.json + signal.json from an existing backtest run.

    Ops (AIMM_API_KEY): pass strategy_id + run_id (called by aimm-web-api after Fire Backtest).
    User MCP key: publishes for the bound strategy (run_id optional if already bound).
    """
    if _is_ops_key(request, x_api_key):
        sid = (body.strategy_id or "").strip()
        rid = (body.run_id or "").strip()
        if not sid or not rid:
            raise HTTPException(
                status_code=400,
                detail="strategy_id and run_id are required for ops publish",
            )
        sid = require_safe_id(sid, name="strategy_id")
        rid = require_safe_id(rid, name="run_id")
        set_run_id_for_strategy(
            sid,
            rid,
            estimated_aum_usdt=body.estimated_aum_usdt,
        )
        try:
            published = publish_strategy_run(
                strategy_id=sid,
                run_id=rid,
                symbol=body.symbol,
                estimated_aum_usdt=body.estimated_aum_usdt,
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "strategy_id": sid, **published}

    binding = _require_binding(request, x_api_key)
    try:
        published = publish_from_run(binding, run_id=body.run_id, symbol=body.symbol)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "strategy_id": binding.strategy_id, **published}
