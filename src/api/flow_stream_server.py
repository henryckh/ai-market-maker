"""HTTP + WebSocket server for live/replay FlowEvent streams."""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.requests import Request
from starlette.status import HTTP_401_UNAUTHORIZED

from config.runs_paths import runs_dir as _resolved_runs_dir

from .agent_prompt_routes import router as agent_prompt_router
from .auth_routes import router as auth_router
from .backtest_routes import recover_stale_backtest_jobs
from .backtest_routes import router as backtest_router
from .capabilities_routes import router as capabilities_router
from .config_designer_routes import router as config_designer_router
from .control_plane_secrets import (
    ensure_control_plane_secrets,
    is_usable_secret,
    presented_matches,
)
from .copy_routes import router as copy_router
from .deploy_routes import router as deploy_router
from .engine_routes import router as engine_router
from .follow_routes import router as follow_router
from .leadpage_routes import router as leadpage_router
from .ops_routes import router as ops_router
from .paper_routes import router as paper_router
from .payload_adapter import build_nexus_payload
from .pm_routes import router as pm_router
from .profile_routes import router as profile_router
from .provider_admin_routes import router as provider_admin_router
from .public_provider_routes import router as public_provider_router
from .runtime_settings_routes import router as runtime_settings_router
from .safe_ids import path_under, require_safe_id
from .schema_validation import validate_nexus_payload
from .signal_routes import router as signal_router
from .studio_routes import router as studio_router
from .tools_routes import router as tools_router

_REPO_ROOT_DOTENV = Path(__file__).resolve().parents[2] / ".env"
load_dotenv(dotenv_path=_REPO_ROOT_DOTENV)

logger = logging.getLogger(__name__)

RUNS_DIR = _resolved_runs_dir()
LATEST_RUN_FILE = RUNS_DIR / "latest_run.txt"
LATEST_PAPER_FILE = RUNS_DIR / "latest_paper.txt"
LATEST_BACKTEST_FILE = RUNS_DIR / "latest_backtest.txt"

DEFAULT_TAIL_EVENTS = int((os.getenv("AIMM_UI_TAIL_EVENTS") or "1200").strip() or "1200")
DEFAULT_TAIL_TRACES = int((os.getenv("AIMM_UI_TAIL_TRACES") or "350").strip() or "350")
DEFAULT_TAIL_MESSAGE_LOG = int((os.getenv("AIMM_UI_TAIL_MESSAGES") or "600").strip() or "600")


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    ensure_control_plane_secrets(generate=True)
    # Daemon backtest threads die on restart; flip orphaned job.json out of "running".
    try:
        recover_stale_backtest_jobs(reason="api_startup")
    except Exception:
        logger.exception("failed to recover orphaned backtest jobs")
    yield


_enable_docs = (os.getenv("AIMM_ENABLE_DOCS") or "").strip().lower() in {"1", "true", "yes", "on"}
app = FastAPI(
    title="AI Market Maker Flow Stream",
    version="0.1.0",
    lifespan=_lifespan,
    docs_url="/docs" if _enable_docs else None,
    redoc_url="/redoc" if _enable_docs else None,
    openapi_url="/openapi.json" if _enable_docs else None,
)


def _expected_api_key() -> str | None:
    v = (os.getenv("AIMM_API_KEY") or "").strip()
    return v if is_usable_secret(v) else None


def _extract_presented_key(request: Request) -> str | None:
    # Bearer is also used for user JWTs, so only accept it when it is the API key.
    x = (request.headers.get("x-api-key") or "").strip()
    if x:
        return x
    auth = (request.headers.get("authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        token = auth.split(" ", 1)[1].strip()
        expected = _expected_api_key()
        if token and expected and presented_matches(token, expected):
            return token
    return None


def _is_public_http_path(path: str) -> bool:
    return path == "/health"


def _with_security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers.setdefault("Cache-Control", "no-store")
    return response


@app.middleware("http")
async def api_key_middleware(request: Request, call_next):
    if _is_public_http_path(request.url.path):
        return _with_security_headers(await call_next(request))

    expected = _expected_api_key()
    if expected is None:
        return _with_security_headers(
            JSONResponse(
                {
                    "error": "api_key_not_configured",
                    "hint": "Set AIMM_API_KEY or run python -m api.control_plane_secrets --write",
                },
                status_code=503,
            )
        )

    presented = _extract_presented_key(request)
    if not presented_matches(presented, expected):
        return _with_security_headers(
            JSONResponse(
                {
                    "error": "unauthorized",
                    "hint": "Set x-api-key (or Authorization: Bearer with the API key)",
                },
                status_code=HTTP_401_UNAUTHORIZED,
            )
        )

    return _with_security_headers(await call_next(request))


_cors_origins_raw = (os.getenv("AIMM_CORS_ORIGINS") or "").strip()
_cors_allow_origins = (
    ["*"]
    if _cors_origins_raw == "*"
    else [o.strip() for o in _cors_origins_raw.split(",") if o.strip()]
)
if _cors_allow_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_allow_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "x-api-key", "x-aimm-dashboard"],
    )
app.include_router(backtest_router)
app.include_router(agent_prompt_router)
app.include_router(runtime_settings_router)
app.include_router(pm_router)
app.include_router(leadpage_router)
app.include_router(auth_router)
app.include_router(provider_admin_router)
app.include_router(signal_router)
app.include_router(follow_router)
app.include_router(copy_router)
app.include_router(paper_router)
app.include_router(public_provider_router)
app.include_router(studio_router)
app.include_router(tools_router)
app.include_router(capabilities_router)
app.include_router(ops_router)
app.include_router(profile_router)
app.include_router(deploy_router)
app.include_router(config_designer_router)
app.include_router(engine_router)


def _resolve_run_log(run_id: str) -> Path:
    """Resolve run id / lane aliases to an events jsonl path."""
    try:
        from api.desk_ownership import resolve_alias

        resolved = resolve_alias(run_id)
        if resolved:
            rid = require_safe_id(resolved, name="run_id")
            return path_under(RUNS_DIR, f"{rid}.events.jsonl")
    except HTTPException:
        raise
    except Exception:
        pass
    rid = require_safe_id((run_id or "").strip(), name="run_id")
    if rid == "latest" and LATEST_RUN_FILE.exists():
        latest_raw = LATEST_RUN_FILE.read_text().strip()
        if latest_raw:
            latest = require_safe_id(latest_raw, name="run_id")
            if latest and not latest.lower().startswith("bt"):
                return path_under(RUNS_DIR, f"{latest}.events.jsonl")
    if rid in ("latest-paper", "latest_paper", "paper") and LATEST_PAPER_FILE.exists():
        latest_raw = LATEST_PAPER_FILE.read_text().strip()
        if latest_raw:
            latest = require_safe_id(latest_raw, name="run_id")
            return path_under(RUNS_DIR, f"{latest}.events.jsonl")
    if rid in ("latest-backtest", "latest_backtest", "backtest") and LATEST_BACKTEST_FILE.exists():
        latest_raw = LATEST_BACKTEST_FILE.read_text().strip()
        if latest_raw:
            latest = require_safe_id(latest_raw, name="run_id")
            return path_under(RUNS_DIR, f"{latest}.events.jsonl")
    return path_under(RUNS_DIR, f"{rid}.events.jsonl")


@app.get("/health")
def health() -> dict[str, Any]:
    from config.llm_env import llm_key_available

    return {
        "ok": True,
        "llm_configured": llm_key_available(),
        "futu_required": False,
    }


@app.get("/futu/price")
def futu_price(
    symbol: str = Query("HK.00700"),
    interval: str = Query("1d"),
    limit: int = Query(200, ge=1, le=2000),
) -> JSONResponse:
    """Historical OHLCV from Futu OpenD for the web Futu console.

    Next.js ``/api/futu/price`` proxies here when ``FLOW_API_BASE_URL`` points at this server.
    """
    from adapters.futu import FutuAdapter

    adapter: FutuAdapter | None = None
    try:
        adapter = FutuAdapter()
        bars = adapter.get_history_kline(symbol=symbol, interval=interval, limit=limit)
    except Exception as exc:  # noqa: BLE001 — return JSON error to the UI proxy
        logger.warning("GET /futu/price failed symbol=%s interval=%s: %s", symbol, interval, exc)
        return JSONResponse(
            {
                "error": "futu_price_failed",
                "detail": str(exc),
                "hint": "Run Futu OpenD (quote port 11111), install futu-api, set FUTU_OPEND_HOST / FUTU_OPEND_QUOTE_PORT.",
            },
            status_code=502,
        )
    finally:
        if adapter is not None:
            adapter.close()

    return JSONResponse({"bars": bars, "symbol": symbol, "source": "flow"})


@app.get("/futu/status")
def futu_status() -> JSONResponse:
    """Quote channel health for Futu OpenD (used by the web Futu console status badge)."""
    from adapters.futu import FutuAdapter, FutuEnvConfig

    cfg = FutuEnvConfig.from_env()
    meta: dict[str, Any] = {
        "host": cfg.host,
        "quote_port": cfg.quote_port,
        "trade_port": cfg.trade_port,
        "source": "flow",
    }
    adapter: FutuAdapter | None = None
    try:
        adapter = FutuAdapter()
        hc = adapter.healthcheck()
    except Exception as exc:  # noqa: BLE001
        logger.warning("GET /futu/status failed: %s", exc)
        return JSONResponse(
            {
                **meta,
                "status": "error",
                "opend_connected": False,
                "detail": str(exc),
            }
        )
    finally:
        if adapter is not None:
            adapter.close()

    return JSONResponse({**meta, **hc})


@app.get("/runs/latest")
def latest_run() -> dict[str, Any]:
    if not LATEST_RUN_FILE.exists():
        return {"run_id": None}
    return {"run_id": LATEST_RUN_FILE.read_text().strip() or None}


@app.get("/runs/{run_id}/events")
def run_events(
    run_id: str, tail: int = Query(DEFAULT_TAIL_EVENTS, ge=50, le=200_000)
) -> JSONResponse:
    log_path = _resolve_run_log(run_id)
    _, events = build_nexus_payload(log_path, tail_events=int(tail))
    return JSONResponse({"run_id": log_path.stem.replace(".events", ""), "events": events})


@app.get("/runs/{run_id}/payload")
def run_payload(
    run_id: str,
    soft: bool = Query(False),
    tail_events: int = Query(DEFAULT_TAIL_EVENTS, ge=50, le=200_000),
    tail_traces: int = Query(DEFAULT_TAIL_TRACES, ge=50, le=50_000),
    tail_messages: int = Query(DEFAULT_TAIL_MESSAGE_LOG, ge=50, le=100_000),
) -> JSONResponse:
    """Nexus UI payload from flow events.

    Use ``soft=1`` while a run is in progress (schema may be loose).
    """
    log_path = _resolve_run_log(run_id)
    payload, _ = build_nexus_payload(
        log_path,
        tail_events=int(tail_events),
        tail_traces=int(tail_traces),
        tail_message_log=int(tail_messages),
    )
    if not soft:
        validate_nexus_payload(payload)
    return JSONResponse(payload)


@app.websocket("/ws/runs/{run_id}")
async def ws_run_payload(websocket: WebSocket, run_id: str) -> None:
    expected = _expected_api_key()
    if expected is None:
        await websocket.close(code=1011)
        return
    presented = (websocket.headers.get("x-api-key") or "").strip()
    if not presented:
        auth = (websocket.headers.get("authorization") or "").strip()
        if auth.lower().startswith("bearer "):
            presented = auth.split(" ", 1)[1].strip()
    if not presented_matches(presented, expected):
        await websocket.close(code=1008)
        return
    await websocket.accept()
    try:
        while True:
            log_path = _resolve_run_log(run_id)
            payload, _ = build_nexus_payload(
                log_path,
                tail_events=DEFAULT_TAIL_EVENTS,
                tail_traces=DEFAULT_TAIL_TRACES,
                tail_message_log=DEFAULT_TAIL_MESSAGE_LOG,
            )
            # Validate payload shape but keep it cheap by validating only the trimmed payload.
            validate_nexus_payload(payload)
            await websocket.send_json({"type": "payload", "payload": payload})
            await asyncio.sleep(1.0)
    except WebSocketDisconnect:
        return
