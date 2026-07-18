# Health MCP

`health-mcp` is a small MCP-facing bridge for Everday health data.

It stores linked-account state locally, talks to an Everday backend over HTTP, and exposes read/write health tools for meal logs, workouts, measurements, insights, goals, history, and weekly review workflows.

## What It Does

- Links an external identity to an Everday user
- Encrypts refresh tokens before storing them in SQLite
- Serves MCP-compatible health tools over HTTP
- Separates read-only, idempotent write, and destructive tool groups

## Required Environment

- `HEALTH_MCP_EVERDAY_BASE_URL`
- `HEALTH_MCP_ENCRYPTION_KEY`

## Common Optional Environment

- `HEALTH_MCP_HOST` default `0.0.0.0`
- `HEALTH_MCP_PORT` default `8766`
- `HEALTH_MCP_PROVIDER` default `authelia`
- `HEALTH_MCP_PUBLIC_BASE_URL`
- `HEALTH_MCP_STATE_DB_PATH` default `/data/health_mcp.sqlite3`
- `HEALTH_MCP_TIMEOUT_SECONDS`
- `HEALTH_MCP_MAX_REQUEST_BYTES`
- `HEALTH_MCP_LINK_SESSION_TTL_MINUTES`

## Files

- `app.py` service implementation
- `Dockerfile` container build for deployment

## Run

```bash
docker build -t health-mcp .
docker run --rm -p 8766:8766 \
  -e HEALTH_MCP_EVERDAY_BASE_URL=http://everday:8000 \
  -e HEALTH_MCP_ENCRYPTION_KEY=replace-me \
  health-mcp
```
