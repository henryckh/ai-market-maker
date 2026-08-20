## Production deployment (platform stack)

This repo can run as a small production platform:
- Postgres (results, signals, follows, inbox, executions)
- API service (FastAPI)
- Worker (fanout + optional auto-execute)
- Web dashboard (Next.js)

### Requirements
- Docker + Docker Compose
- Unique `AIMM_API_KEY`, `AIMM_AUTH_SECRET`, and `POSTGRES_PASSWORD`. If you leave them
  empty, `docker compose` generates them into `./.secrets/` on first boot.

### Environment variables
Create a `.env` next to `docker-compose.prod.yml`:

```bash
# Database (empty POSTGRES_PASSWORD = generate into .secrets/)
POSTGRES_PASSWORD=
# DATABASE_URL=

# Auth (empty = auto-generate into .secrets/)
AIMM_ENV=production
AIMM_AUTH_SECRET=
AIMM_API_KEY=

# Web -> API
FLOW_API_BASE_URL=http://api:8001

# AIMM_CORS_ORIGINS=

# Worker
PLATFORM_WORKER_AUTO_EXECUTE=0
PLATFORM_WORKER_INTERVAL_SEC=2.0
PLATFORM_WORKER_BATCH=200
PLATFORM_WORKER_CURSOR=default
```

The dashboard talks to Flow through Next.js (`/api/flow`) so the API key stays
server-side. Direct calls to the FastAPI port need `x-api-key` (see `.secrets/api_key`).

### Start

```bash
docker compose -f docker-compose.prod.yml up -d --build
```

### Database migrations (recommended)

Run once (or on each release) before starting traffic:

```bash
docker compose -f docker-compose.prod.yml run --rm api alembic upgrade head
```

Notes:
- The runtime DB auto-create is disabled in production compose (`AIMM_DB_AUTOCREATE=0`).
- If you want dev convenience, set `AIMM_DB_AUTOCREATE=1` locally.

### Operational endpoints
- API health: `GET /health` (unauthenticated)
- Latest run payload: `GET /runs/latest/payload` (requires `x-api-key`)
- Leaderboard: `GET /leadpage/leaderboard` (requires `x-api-key`)
- Feed: `GET /signals/feed` (requires `x-api-key`)

### Security checklist
- Leave `AIMM_API_KEY` / `AIMM_AUTH_SECRET` empty to generate unique keys, or set your own long random values
- Keep `.secrets/` private and out of git
- Dashboard and API bind to `127.0.0.1` by default; override `AIMM_WEB_PUBLISH` / `AIMM_API_PUBLISH` only behind TLS
- Put a reverse proxy with TLS in front of any public bind
- Leave `AIMM_CORS_ORIGINS` empty unless a browser must call the API cross-origin
- Keep `PLATFORM_WORKER_AUTO_EXECUTE=0` unless you explicitly want it
- Set `POSTGRES_PASSWORD` to a long random value, or leave it empty to generate one (do not map `5433` to `0.0.0.0`)
