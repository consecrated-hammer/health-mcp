# Health MCP

`health-mcp` is a small MCP-facing bridge for Everday health data.

It stores linked-account state locally, talks to an Everday backend over HTTP, and exposes read/write health tools for meal logs, workouts, measurements, insights, goals and targets, history, recipe and product reviews, experiments, and weekly review workflows.

Recipe and product reviews are full read/add/edit tools: `upsert_recipe_review` and `upsert_product_review` create a new review or update an existing one (pass the review's id to update), and `get_recipe_reviews` / `get_recipe_stats` / `get_product_reviews` read them back.

## What It Does

- Links an external identity to an Everday user
- Encrypts refresh tokens before storing them in SQLite
- Serves MCP-compatible health tools over HTTP
- Updates current calorie, macro, step, and sodium targets through `update_targets`
- Adds task awareness after successful Health MCP reads and writes: all overdue Health tasks plus tasks due in the next two hours
- Flags a missing weigh-in to the agent when the latest logged weight is eight or more days old
- Surfaces a newly flagged resting-heart-rate reading once to the agent, with the reading's date and contextual reminder
- Reminds the agent that the weekly review is due from Sunday evening through Monday morning
- Reminds the agent to add dashboard notes after dinner, and again the following day while those notes remain blank
- Reminds the agent to capture hunger-before-dinner and satisfaction scores after dinner, while either score is missing
- Reminds the agent to record weekday work location; period-status reminders are limited to a cycle window inferred from prior logs
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
