"""Static OlaXBT Datalayer API docs snapshot for MCP explore tool."""

from __future__ import annotations

from typing import Any


def datalayer_api_docs_snapshot() -> dict[str, Any]:
    """Return a compact, agent-readable catalog of Datalayer capabilities.

    This is intentionally static so MCP responses stay low-latency and do not
    depend on the Datalayer service being reachable at call time.
    """
    return {
        "service": "OlaXBT Datalayer API",
        "default_base_url": "https://api-data.olaxbt.xyz",
        "auth": {
            "headers": [
                "Authorization: Bearer <JWT>",
                "x-api-key: <nxk_… user key or server key>",
            ],
            "notes": "Skills /v1 routes accept JWT or x-api-key. Historical Nexus used by AIMM prefers DATALAYER_API_URL.",
        },
        "discovery": {
            "meta": "GET /api/v1/meta",
            "openapi": "GET /api/v1/openapi.json",
            "docs_catalog": "GET /api/v1/docs/catalog.json",
            "postman": "GET /api/v1/docs/postman.json",
        },
        "endpoints": [
            {
                "group": "historical_nexus",
                "path": "GET /api/v1/historical/nexus",
                "description": "Point-in-time Nexus bundle for backtests (OHLCV + alt features).",
                "query": ["symbol", "ts", "as_of"],
            },
            {
                "group": "historical_nexus",
                "path": "GET /api/v1/historical/funding",
                "description": "Historical funding rates.",
            },
            {
                "group": "historical_nexus",
                "path": "GET /api/v1/historical/fear-greed",
                "description": "Fear & Greed index history.",
            },
            {
                "group": "historical_nexus",
                "path": "GET /api/v1/historical/date-range",
                "description": "Available snapshot date coverage.",
            },
            {
                "group": "macro_fred",
                "path": "GET /api/v1/... (FRED series via skills catalog)",
                "description": "US macro / Fed-related series for Monetary Sentinel desks.",
                "hint": "Use GET /api/v1/docs/catalog.json for exact FRED paths in the deployed environment.",
            },
            {
                "group": "sentiment",
                "path": "GET /api/sentiment",
                "description": "Daily market sentiment aggregate; optional ?symbol=BTC.",
            },
            {
                "group": "sentiment",
                "path": "GET /api/sentiment/trends",
                "description": "Sentiment trends over time.",
            },
            {
                "group": "news",
                "path": "GET /api/news",
                "description": "Aggregated crypto news (BlockBeats, CryptoCompare, CoinGecko).",
                "query": ["limit", "source", "crypto", "hours"],
            },
            {
                "group": "onchain_defi",
                "path": "DefiLlama / liquidity proxies via skills catalog",
                "description": "Chain TVL and liquidity context for agentic desks.",
            },
            {
                "group": "derivatives",
                "path": "GET /api/.../coinglass/*",
                "description": "OI, funding, liquidations, whale positioning (CoinGlass-backed).",
            },
            {
                "group": "etf",
                "path": "GET /api/etf/inflow",
                "description": "BTC/ETH ETF inflow history.",
            },
        ],
        "agent_usage_notes": [
            "Prefer historical Nexus endpoints for reproducible backtests; live endpoints for paper/live desks.",
            "Do not call Datalayer from inside MCP tool handlers — explore docs here, then call Datalayer from the agent runtime.",
            "Exchange private keys never leave OlaXBT; MCP only returns metrics and copy-trade intents.",
        ],
        "related_env": {
            "DATALAYER_API_URL": "Base URL used by henry-ai-market-maker Nexus remote provider",
            "NEXUS_PROVIDER_MODE": "remote | local | off",
        },
    }
