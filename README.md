# Health MCP

`health-mcp` is a small MCP-facing bridge for Everday health data.

It stores linked-account state locally, talks to an Everday backend over HTTP, and exposes read/write health tools for meal logs, workouts, headaches, medication doses, measurements, insights, goals and targets, history, recipe and product reviews, experiments, and weekly review workflows.

Recipe and product reviews are full read/add/edit tools: `upsert_recipe_review` and `upsert_product_review` create a new review or update an existing one (pass the review's id to update), and `get_recipe_reviews` / `get_recipe_stats` / `get_product_reviews` read them back.

## What It Does

- Links an external identity to an Everday user
- Encrypts refresh tokens before storing them in SQLite
- Serves MCP-compatible health tools over HTTP
- Reads active health goals through `get_goals`, previews recommendations through `preview_goal_recommendation`, and creates or replaces outcome goals through `set_goal` without changing active targets
- Updates current calorie, macro, step, and sodium targets through `update_targets`
- Logs idempotent headache events and medication doses, including an optional medication dose linked to a headache
- Adds task awareness after successful Health MCP reads and writes: all overdue Health tasks plus tasks due in the next two hours
- Flags a missing weigh-in to the agent when the latest logged weight is eight or more days old
- Surfaces a newly flagged resting-heart-rate reading once to the agent, with the reading's date and contextual reminder
- Reminds the agent that the weekly review is due from Sunday evening through Monday morning
- Reminds the agent to add dashboard notes after dinner, and again the following day while those notes remain blank
- Reminds the agent to capture hunger-before-dinner and satisfaction scores after dinner, while either score is missing
- Reminds the agent to record weekday work location; period-status reminders are limited to a cycle window inferred from prior logs
- Separates read-only, idempotent write, and destructive tool groups

## Protocol Versions

The MCP surface runs on the official Python SDK v2 and serves the modern
`2026-07-28` protocol revision. The SDK also retains its supported 2025-era
handshake path on the same `/mcp` endpoint so existing ChatGPT and Claude
connections continue to work while clients adopt the modern stateless
protocol. A legacy client requesting `2025-06-18` still receives `2025-06-18`;
the SDK's current legacy client defaults to `2025-11-25` and receives that
revision instead of being negotiated down.

Health MCP does not maintain its own protocol negotiation, JSON-RPC dispatch,
header validation, sessions, or result envelopes. Those are SDK-owned
contracts. The application owns the 62-tool catalogue, schemas and annotations;
the OAuth gateway owns public authentication and forwards the authenticated
identity headers consumed by tool handlers.

The SDK's low-level `Server` is intentional here: Health MCP already has an
explicit, tested JSON Schema catalogue and uniform `(arguments, headers)` tool
handlers. This keeps that application contract intact while delegating the wire
protocol and transport to SDK v2.

The transport is configured with JSON responses and stateless legacy HTTP.
Modern `2026-07-28` requests are stateless by definition. The service does not
currently use MRTR, Tasks, MCP Apps, resources, prompts, or subscription
notifications.

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
- `HEALTH_MCP_ALLOWED_ORIGINS` comma-separated allowlist checked against the
  `Origin` header on `/mcp`. Unset means allow any origin, which is the
  deployed configuration: the service is reached through the OAuth gateway
  rather than directly by a browser.

## Files

- `app.py` Health domain, Everday integration, linked-account state, and tool catalogue
- `server.py` Python SDK v2 MCP transport and public route composition
- `requirements.txt` pinned runtime dependencies
- `Dockerfile` container build for deployment
- `tests/` domain and SDK contract tests, run with `unittest`

## Test

The checked-in `.venv` has neither `pytest` nor `cryptography`, so run the suite
in the runtime image, which has what `app.py` imports:

```bash
docker run --rm -v "$PWD":/src -w /src \
  -e HEALTH_MCP_EVERDAY_BASE_URL=http://everday.test \
  -e HEALTH_MCP_STATE_DB_PATH=/tmp/t.sqlite3 \
  -e HEALTH_MCP_ENCRYPTION_KEY=MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA= \
  --entrypoint python health-mcp:local -m unittest discover -s tests -v
```

## Run

```bash
docker build -t health-mcp .
docker run --rm -p 8766:8766 \
  -e HEALTH_MCP_EVERDAY_BASE_URL=http://everday:8000 \
  -e HEALTH_MCP_ENCRYPTION_KEY=replace-me \
  health-mcp
```
