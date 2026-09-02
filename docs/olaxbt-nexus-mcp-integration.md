# OlaXBT Nexus — Strategy API (MCP)

**Canonical docs (HTML):** https://nexus.olaxbt.xyz/api/mcp/docs  
**Catalog:** https://nexus.olaxbt.xyz/api/mcp/docs/catalog.json  
**Integration markdown (product copy):** `aimm-web-api/docs/olaxbt-nexus-mcp-integration.md`

This file is a pointer for engine contributors. Host / XAgent teams should use the **live HTML** above.

## Deploy JSON — who configures what

| Who | How |
|-----|-----|
| End users | **Nexus Studio** Strategy Builder (`aimm-config/v4`) — desks, weights, LLM flags, risk |
| MCP callers | `run_backtest` with optional `n_bars` only — **no** `agents` / `execution` body |
| Engine ops | `config/deploy.*.json` + [agentic-config.md](./agentic-config.md) |

MCP reuses the last Studio-compiled deploy stored on the prior Flow job.

## Engine tools surface

Implemented in `src/api/mcp.py` (mounted on Flow). Public gateway: aimm-web-api `GET|POST /api/mcp/*`.

Do not treat `aimm-web-api/public/mcp-docs.html` as source of truth — live docs are rendered from `catalog.ts` via `GET /api/mcp/docs`.
