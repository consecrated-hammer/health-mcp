import hashlib
import importlib.metadata
import json
import logging
import urllib.parse
from contextlib import asynccontextmanager
from typing import Any

import anyio
import uvicorn
from mcp import types
from mcp.server.lowlevel.server import Server, ServerRequestContext
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Route

import app as health
import mcp_apps
from output_schemas import OUTPUT_SCHEMAS


# Use Uvicorn's configured operational logger so INFO telemetry is emitted in
# production without adding a second handler or changing global log settings.
logger = logging.getLogger("uvicorn.error")
UI_EXTENSION_ID = "io.modelcontextprotocol/ui"
MCP_SDK_VERSION = importlib.metadata.version("mcp")


def _server_info(_arguments: dict[str, Any], _headers: Any) -> dict[str, Any]:
    return {
        "Server": health._server_identity(),
        "McpSdkVersion": MCP_SDK_VERSION,
        "UiExtension": {
            "Identifier": UI_EXTENSION_ID,
            "MimeTypes": [mcp_apps.RESOURCE_MIME_TYPE],
        },
        "ToolCatalogues": {
            "TextOnly": len(health.TOOLS) + len(SERVER_TOOLS),
            "WithApps": len(health.TOOLS) + len(SERVER_TOOLS) + len(mcp_apps.APP_TOOLS),
        },
    }


SERVER_TOOLS: dict[str, dict[str, Any]] = {
    "server_info": {
        "description": "Return the Health MCP service build, SDK version, UI extension declaration, and capability-aware tool counts for diagnostics.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        "outputSchema": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {
                "Server": {"type": "object"},
                "McpSdkVersion": {"type": "string"},
                "UiExtension": {"type": "object"},
                "ToolCatalogues": {"type": "object"},
            },
            "required": ["Server", "McpSdkVersion", "UiExtension", "ToolCatalogues"],
            "additionalProperties": False,
        },
        "annotations": {
            "title": "Server Info",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "handler": _server_info,
    }
}


def _has_actionable_awareness(awareness: dict[str, Any]) -> bool:
    return bool(
        awareness.get("Overdue")
        or awareness.get("Upcoming")
        or (awareness.get("WeeklyReview") or {}).get("Due") is True
        or any(
            (awareness.get(key) or {}).get("NeedsLogging") is True
            for key in (
                "WeightReminder",
                "DinnerReflectionReminder",
                "DailyDetailsReminder",
            )
        )
    )


@asynccontextmanager
async def _lifespan(_server: Server):
    health._init_db()
    yield None


def _tool_descriptors(*, include_apps: bool = True) -> list[types.Tool]:
    specs = {**health.TOOLS, **SERVER_TOOLS, **(mcp_apps.APP_TOOLS if include_apps else {})}

    def description(name: str, value: str) -> str:
        if include_apps:
            return value
        if name == "get_today_summary":
            return (
                value
                + " This client does not support interactive Health Apps. Present the returned Entries "
                "as a readable meal-by-meal food log in the assistant response; never claim the result "
                "is displayed in a card or shown above."
            )
        return value

    return [
        types.Tool(
            name=name,
            description=description(name, spec["description"]),
            inputSchema=spec["inputSchema"],
            outputSchema=spec.get("outputSchema") or OUTPUT_SCHEMAS[name],
            annotations=types.ToolAnnotations.model_validate(
                spec.get("annotations") or health._tool_annotations(name)
            ),
            _meta=mcp_apps.tool_meta(name, spec.get("_meta")) if include_apps else None,
        )
        for name, spec in specs.items()
    ]


def _client_supports_apps(context: ServerRequestContext[Any, Request]) -> bool:
    capabilities = context.session.client_capabilities
    extensions = capabilities.extensions if capabilities is not None else None
    return UI_EXTENSION_ID in (extensions or {})


def _client_capability_telemetry(context: ServerRequestContext[Any, Request]) -> dict[str, Any]:
    client_params = context.session.client_params
    client_info = client_params.client_info if client_params is not None else None
    capabilities = context.session.client_capabilities
    capability_names: list[str] = []
    extension_names: list[str] = []
    if capabilities is not None:
        capability_names = sorted(
            name
            for name in capabilities.__class__.model_fields
            if name != "extensions" and getattr(capabilities, name, None) is not None
        )
        extension_names = sorted((capabilities.extensions or {}).keys())
    return {
        "event": "mcp_client_capabilities",
        "protocol_version": context.protocol_version,
        "client_name": client_info.name if client_info is not None else None,
        "client_version": client_info.version if client_info is not None else None,
        "capabilities": capability_names,
        "extensions": extension_names,
    }


def _log_client_capabilities(context: ServerRequestContext[Any, Request]) -> None:
    # Deliberately excludes request arguments, identity headers, and tool names:
    # this log exists only to show which optional MCP features clients advertise.
    logger.info(
        "mcp_client_capabilities %s",
        json.dumps(_client_capability_telemetry(context), sort_keys=True),
    )


def _tool_call_telemetry(
    context: ServerRequestContext[Any, Request],
    name: str,
    result: Any,
    is_error: bool,
) -> dict[str, Any]:
    headers = context.request.headers if context.request is not None else {}
    subject = str(headers.get("X-Auth-Request-Sub") or "").strip()
    account_key = hashlib.sha256(subject.encode()).hexdigest()[:12] if subject else None
    result_record = result if isinstance(result, dict) else {}
    entries = result_record.get("Entries")
    daily_log = result_record.get("DailyLog")
    log_date = (
        daily_log.get("LogDate")
        if isinstance(daily_log, dict)
        else result_record.get("LogDate") or result_record.get("TargetLogDate")
    )
    return {
        "event": "mcp_tool_call",
        "tool": name,
        "protocol_version": context.protocol_version,
        "app_supported": _client_supports_apps(context),
        "is_error": is_error,
        "account_key": account_key,
        "result_keys": sorted(result_record),
        "entry_count": len(entries) if isinstance(entries, list) else None,
        "log_date": log_date,
    }


def _log_tool_call(
    context: ServerRequestContext[Any, Request],
    name: str,
    result: Any,
    is_error: bool,
) -> None:
    # No arguments, food text, display names, email addresses, or raw subjects.
    logger.info(
        "mcp_tool_call %s",
        json.dumps(_tool_call_telemetry(context, name, result, is_error), sort_keys=True),
    )


async def _list_tools(
    context: ServerRequestContext[Any, Request],
    _params: types.PaginatedRequestParams | None,
) -> types.ListToolsResult:
    _log_client_capabilities(context)
    return types.ListToolsResult(tools=_tool_descriptors(include_apps=_client_supports_apps(context)))


def _invoke_tool(
    name: str,
    arguments: dict[str, Any],
    headers: Any,
    *,
    include_apps: bool = True,
) -> tuple[Any, bool]:
    spec = health.TOOLS.get(name) or SERVER_TOOLS.get(name) or mcp_apps.APP_TOOLS.get(name)
    if spec is None:
        return {"error": f"Unknown tool: {name}"}, True

    try:
        result = spec["handler"](arguments, headers)
        if isinstance(result, dict) and name not in SERVER_TOOLS:
            awareness = health._task_awareness(headers)
            if awareness is not None:
                has_action_form = _has_actionable_awareness(awareness)
                if has_action_form:
                    awareness["ActionForm"] = {"Required": True}
                suppresses_action_app = name in (
                    mcp_apps.ACTION_FORM_RENDERING_TOOLS
                    | mcp_apps.FOOD_LOG_MUTATION_TOOLS
                    | {"app_complete_health_actions"}
                )
                if include_apps and not suppresses_action_app and has_action_form:
                    awareness["ActionApp"] = {
                        "Tool": "app_complete_health_actions",
                        "Required": True,
                    }
                result["TaskAwareness"] = awareness
                if awareness.get("AgentNotice"):
                    app_instruction = (
                        " Call app_complete_health_actions now so the user can complete these items."
                        if awareness.get("ActionApp")
                        else (
                            " Present these outstanding items directly in the assistant response; do not call an App."
                            if has_action_form
                            and (not include_apps or name in mcp_apps.FOOD_LOG_MUTATION_TOOLS)
                            else ""
                        )
                    )
                    result["AgentNotice"] = awareness["AgentNotice"] + app_instruction
        return result, False
    except (ValueError, RuntimeError) as exc:
        return {"error": str(exc)}, True
    except Exception as exc:  # noqa: BLE001
        return {"error": f"Unhandled tool failure: {exc}"}, True


async def _call_tool(
    context: ServerRequestContext[Any, Request],
    params: types.CallToolRequestParams,
) -> types.CallToolResult:
    _log_client_capabilities(context)
    headers = context.request.headers if context.request is not None else {}
    result, is_error = await anyio.to_thread.run_sync(
        lambda: _invoke_tool(
            params.name,
            params.arguments or {},
            headers,
            include_apps=_client_supports_apps(context),
        )
    )
    _log_tool_call(context, params.name, result, is_error)
    text = json.dumps(result, ensure_ascii=True, indent=2, default=str)
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=text)],
        structuredContent=result,
        isError=is_error,
    )


async def _list_resources(
    context: ServerRequestContext[Any, Request],
    _params: types.PaginatedRequestParams | None,
) -> types.ListResourcesResult:
    _log_client_capabilities(context)
    return types.ListResourcesResult(resources=mcp_apps.resources())


async def _read_resource(
    context: ServerRequestContext[Any, Request],
    params: types.ReadResourceRequestParams,
) -> types.ReadResourceResult:
    _log_client_capabilities(context)
    return mcp_apps.read_resource(str(params.uri))


mcp_server = Server(
    name="health-mcp",
    version=health.Config.version,
    instructions=(
        "Health tools for Everday: meal, weight, workout, headache, medication, and daily log reads and writes, "
        "plus Health-linked task management. Most tools require a linked account. When a tool result contains "
        "TaskAwareness.ActionApp.Required=true, immediately call the named ActionApp.Tool so the user receives "
        "the conditional completion form. When DashboardNotesReminder.AgentAction.Required=true, treat each date "
        "in AgentAction.Dates as agent work: read that day with the named read tool when needed, write a concise "
        "factual Notes summary with the named write tool, and ask the user only when the available data is "
        "genuinely insufficient."
    ),
    lifespan=_lifespan,
    on_list_tools=_list_tools,
    on_call_tool=_call_tool,
    on_list_resources=_list_resources,
    on_read_resource=_read_resource,
)
mcp_server.extensions[UI_EXTENSION_ID] = {"mimeTypes": [mcp_apps.RESOURCE_MIME_TYPE]}


async def _healthz(_request: Request) -> Response:
    return PlainTextResponse("ok\n")


async def _version(_request: Request) -> Response:
    return JSONResponse(
        {
            "service": "health-mcp",
            "version": health.Config.version,
            "everday_base_url": health.Config.everday_base_url,
            "tools": sorted({*health.TOOLS, *SERVER_TOOLS, *mcp_apps.APP_TOOLS}),
            "resources": sorted(mcp_apps.RESOURCE_DESCRIPTIONS),
        }
    )


async def _root(_request: Request) -> Response:
    return JSONResponse({"service": "health-mcp", "version": health.Config.version})


def _link_error(status: int, message: str) -> Response:
    fake_session = {
        "external_name": None,
        "external_email": None,
        "external_subject": "this account",
        "expires_at": "",
        "status": "error",
        "last_error": message,
        "everday_username": None,
        "everday_user_id": None,
    }
    return Response(
        health._render_link_page(session=fake_session),  # type: ignore[arg-type]
        status_code=status,
        media_type="text/html",
    )


async def _link(request: Request) -> Response:
    session_token = request.path_params["session_token"].strip("/")
    if not session_token:
        return _link_error(404, "This account-link session does not exist.")
    if request.method == "GET":
        return await anyio.to_thread.run_sync(_link_get, session_token)
    try:
        content_length = int(request.headers.get("Content-Length", "0") or "0")
    except (TypeError, ValueError):
        content_length = 0
    if content_length <= 0 or content_length > 16384:
        return await anyio.to_thread.run_sync(_link_post, session_token, b"")
    raw = await request.body()
    return await anyio.to_thread.run_sync(_link_post, session_token, raw)


def _link_get(session_token: str) -> Response:
    session = health._load_link_session(session_token)
    if session is None:
        return _link_error(404, "This account-link session does not exist.")
    if session["status"] == "pending":
        try:
            session = health._active_link_session_or_error(session_token)
        except ValueError as exc:
            session = health._load_link_session(session_token) or session
            return Response(
                health._render_link_page(session=session, error_message=str(exc)),
                status_code=410,
                media_type="text/html",
            )

    linked_profile = None
    if session["status"] == "completed":
        account = health._load_account(session["external_subject"])
        if account is not None:
            try:
                access_token, _ = health._refresh_access_for_principal(
                    {
                        "subject": session["external_subject"],
                        "email": session["external_email"],
                        "name": session["external_name"],
                    }
                )
                linked_profile = health._everday_profile(access_token)
            except Exception:  # noqa: BLE001
                linked_profile = None
    return Response(
        health._render_link_page(session=session, linked_profile=linked_profile),
        media_type="text/html",
    )


def _link_post(session_token: str, raw: bytes) -> Response:
    try:
        session = health._active_link_session_or_error(session_token)
    except ValueError as exc:
        existing = health._load_link_session(session_token)
        if existing is None:
            return _link_error(404, str(exc))
        return Response(
            health._render_link_page(session=existing, error_message=str(exc)),
            status_code=410,
            media_type="text/html",
        )

    if not raw or len(raw) > 16384:
        return Response(
            health._render_link_page(session=session, error_message="The submitted form was invalid."),
            status_code=400,
            media_type="text/html",
        )

    form = urllib.parse.parse_qs(raw.decode("utf-8", errors="replace"), keep_blank_values=False)
    username = (form.get("username") or [""])[0].strip()
    password = (form.get("password") or [""])[0]
    if not username or not password:
        return Response(
            health._render_link_page(
                session=session,
                error_message="Everday username and password are required.",
            ),
            status_code=400,
            media_type="text/html",
        )

    try:
        login = health._everday_login(username, password)
        access_token = str(login.get("AccessToken") or "").strip()
        refresh_token = str(login.get("RefreshToken") or "").strip()
        if not access_token or not refresh_token:
            raise RuntimeError("Everday login did not return usable tokens.")
        profile = health._everday_profile(access_token)
        everday_user_id = int(profile.get("UserId"))
        health._save_account(
            session["external_subject"],
            session["external_email"],
            session["external_name"],
            everday_user_id,
            username,
            refresh_token,
        )
        health._mark_link_session(
            session_token,
            status="completed",
            everday_user_id=everday_user_id,
            everday_username=username,
            completed_at=health._utc_now().isoformat(),
            last_error=None,
        )
        updated = health._load_link_session(session_token) or session
        return Response(
            health._render_link_page(session=updated, linked_profile=profile),
            media_type="text/html",
        )
    except (ValueError, RuntimeError) as exc:
        health._mark_link_session(
            session_token,
            status="pending",
            everday_user_id=session["everday_user_id"],
            everday_username=session["everday_username"],
            completed_at=session["completed_at"],
            last_error=str(exc),
        )
        refreshed = health._load_link_session(session_token) or session
        return Response(
            health._render_link_page(session=refreshed, error_message=str(exc)),
            status_code=400,
            media_type="text/html",
        )


routes = [
    Route("/healthz", _healthz, methods=["GET"]),
    Route("/version", _version, methods=["GET"]),
    Route("/", _root, methods=["GET"]),
    Route("/link/{session_token:path}", _link, methods=["GET", "POST"]),
]

sdk_app = mcp_server.streamable_http_app(
    streamable_http_path="/mcp",
    json_response=True,
    stateless_http=True,
    max_request_body_size=health.Config.max_request_bytes,
    host=health.Config.host,
    custom_starlette_routes=routes,
)


class App:
    """Preserve the pre-SDK MCP preflight contract around the SDK ASGI app."""

    def __init__(self, wrapped: Any):
        self.wrapped = wrapped
        self.routes = wrapped.routes

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if (
            scope["type"] == "http"
            and scope["method"] == "OPTIONS"
            and scope["path"] == "/mcp"
        ):
            response = Response(status_code=204, headers={"Allow": "POST, OPTIONS"})
            await response(scope, receive, send)
            return
        if (
            scope["type"] == "http"
            and scope["method"] == "POST"
            and scope["path"] == "/mcp"
            and health.Config.allowed_origins
        ):
            headers = {
                key.decode("latin-1").lower(): value.decode("latin-1")
                for key, value in scope["headers"]
            }
            origin = headers.get("origin", "").strip()
            if origin and origin not in health.Config.allowed_origins:
                response = JSONResponse({"error": "forbidden_origin"}, status_code=403)
                await response(scope, receive, send)
                return
        await self.wrapped(scope, receive, send)


app = App(sdk_app)


def main() -> None:
    uvicorn.run(app, host=health.Config.host, port=health.Config.port, log_level="info")


if __name__ == "__main__":
    main()
