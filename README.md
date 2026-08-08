# Health MCP

`health-mcp` is a small MCP-facing bridge for Everday health data.

It stores linked-account state locally, talks to an Everday backend over HTTP, and exposes read/write health tools for meal logs, workouts, measurements, insights, goals and targets, history, recipe and product reviews, experiments, and weekly review workflows.

Recipe and product reviews are full read/add/edit tools: `upsert_recipe_review` and `upsert_product_review` create a new review or update an existing one (pass the review's id to update), and `get_recipe_reviews` / `get_recipe_stats` / `get_product_reviews` read them back.

## What It Does

- Links an external identity to an Everday user
- Encrypts refresh tokens before storing them in SQLite
- Serves MCP-compatible health tools over HTTP
- Reads active health goals through `get_goals`, previews recommendations through `preview_goal_recommendation`, and creates or replaces outcome goals through `set_goal` without changing active targets
- Updates current calorie, macro, step, and sodium targets through `update_targets`
- Adds task awareness after successful Health MCP reads and writes: all overdue Health tasks plus tasks due in the next two hours
- Flags a missing weigh-in to the agent when the latest logged weight is eight or more days old
- Surfaces a newly flagged resting-heart-rate reading once to the agent, with the reading's date and contextual reminder
- Reminds the agent that the weekly review is due from Sunday evening through Monday morning
- Reminds the agent to add dashboard notes after dinner, and again the following day while those notes remain blank
- Reminds the agent to capture hunger-before-dinner and satisfaction scores after dinner, while either score is missing
- Reminds the agent to record weekday work location; period-status reminders are limited to a cycle window inferred from prior logs
- Separates read-only, idempotent write, and destructive tool groups

## Protocol Versions

The server is **dual-era**: it answers both the legacy `2025-06-18` revision and
the modern `2026-07-28` revision on the same `/mcp` endpoint.

The era is selected per request. A request carrying
`_meta["io.modelcontextprotocol/protocolVersion"]` is served as `2026-07-28`;
anything else falls through to the `initialize` handshake path and is served as
`2025-06-18`. Legacy responses are unchanged, so existing clients see no
difference.

Modern requests are validated as the spec requires:

- `MCP-Protocol-Version`, `Mcp-Method`, and `Mcp-Name` (on `tools/call`) must be
  present and must match the request body, including after decoding the
  `=?base64?...?=` sentinel form. Mismatches return `400` with `-32020`.
- An unsupported version returns `400` with `-32022` and the supported list.
- A missing `io.modelcontextprotocol/clientCapabilities` returns `400` with `-32602`.
- An unknown method returns `404` with `-32601`.
- `GET` and `DELETE` on `/mcp` return `405`; sessions and resumable streams are
  not part of either era this server speaks.

`server/discover` is implemented. It advertises `2026-07-28` only, and so does
the `supported` list on a `-32022`: those lists say which versions a client may
select through per-request `_meta`, and the legacy revision is reachable solely
through `initialize`. Listing it would invite a downgrade the modern path
rejects, and on the error would loop a client that retries as the spec directs.

Not implemented: SSE response streams, `subscriptions/listen`, MRTR, and the
Tasks and MCP Apps extensions. Every tool here answers in a single round trip,
so nothing currently needs them.

A legacy `initialize` logs the version the client asked for alongside the
version served, so a client moving to a newer revision is visible in the logs.

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

- `app.py` service implementation
- `Dockerfile` container build for deployment
- `tests/` unit and protocol-conformance tests, run with `unittest`

## Run

```bash
docker build -t health-mcp .
docker run --rm -p 8766:8766 \
  -e HEALTH_MCP_EVERDAY_BASE_URL=http://everday:8000 \
  -e HEALTH_MCP_ENCRYPTION_KEY=replace-me \
  health-mcp
```
