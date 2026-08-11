from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from mcp import types

import app as health
from output_schemas import OUTPUT_SCHEMAS


RESOURCE_MIME_TYPE = "text/html;profile=mcp-app"
TODAY_RESOURCE_URI = "ui://health/today-v2.html"
CHECKIN_RESOURCE_URI = "ui://health/checkin-v2.html"
FOOD_LOG_RESOURCE_URI = "ui://health/food-log-v4.html"
HEALTH_ACTIONS_RESOURCE_URI = "ui://health/actions-v1.html"
_UI_DIST = Path(__file__).resolve().parent / "web" / "dist"


def _app_tool_meta(resource_uri: str, *, invoking: str, invoked: str) -> dict[str, Any]:
    return {
        "ui": {"resourceUri": resource_uri, "visibility": ["model", "app"]},
        "openai/outputTemplate": resource_uri,
        "openai/widgetAccessible": True,
        "openai/toolInvocation/invoking": invoking,
        "openai/toolInvocation/invoked": invoked,
    }


def _linked_date_context(headers: Any) -> tuple[str, str]:
    context = health._tool_get_connection_context({}, headers)
    reminder_timezone = str(context.get("ReminderTimeZone") or "UTC")
    linked_today = health._utc_now().astimezone(
        health._task_timezone(reminder_timezone, "UTC")
    ).date().isoformat()
    return linked_today, reminder_timezone


def _app_show_today_health(arguments: dict[str, Any], headers: Any) -> dict[str, Any]:
    date_value = str(arguments.get("date") or "").strip()
    if not date_value:
        date_value, _timezone = _linked_date_context(headers)
    return health._tool_get_today_summary({"date": date_value}, headers)


def _app_show_food_log(arguments: dict[str, Any], headers: Any) -> dict[str, Any]:
    date_value = str(arguments.get("date") or "").strip()
    if not date_value:
        date_value, _timezone = _linked_date_context(headers)
    return health._tool_get_today_summary({"date": date_value}, headers)


def _app_complete_health_actions(_arguments: dict[str, Any], headers: Any) -> dict[str, Any]:
    linked_today, reminder_timezone = _linked_date_context(headers)
    return {
        "LinkedToday": linked_today,
        "ReminderTimeZone": reminder_timezone,
    }


def _nullable_string(arguments: dict[str, Any], name: str) -> str | None:
    value = str(arguments.get(name) or "").strip()
    return value or None


def _app_prepare_health_checkin(arguments: dict[str, Any], headers: Any) -> dict[str, Any]:
    # The UI needs the linked timezone even when a date was supplied: it uses
    # that zone for editable wall-clock times rather than the browser's current
    # zone, which may differ while the user is travelling.
    linked_today, reminder_timezone = _linked_date_context(headers)
    record_type = str(arguments.get("record_type") or "headache").strip()
    if record_type not in {"headache", "medication"}:
        raise ValueError("record_type must be headache or medication.")

    severity = arguments.get("severity")
    if severity is not None:
        severity = int(severity)
        if severity < 1 or severity > 10:
            raise ValueError("severity must be between 1 and 10.")

    return {
        "Draft": {
            "idempotency_key": _nullable_string(arguments, "idempotency_key")
            or f"health-checkin-{uuid.uuid4()}",
            "record_type": record_type,
            "date": _nullable_string(arguments, "date") or linked_today,
            "timezone": reminder_timezone,
            "onset_at": _nullable_string(arguments, "onset_at"),
            "severity": severity,
            "location": _nullable_string(arguments, "location"),
            "notes": _nullable_string(arguments, "notes"),
            "medication_name": _nullable_string(arguments, "medication_name"),
            "medication_dose": _nullable_string(arguments, "medication_dose"),
            "medication_taken_at": _nullable_string(arguments, "medication_taken_at"),
            "medication_notes": _nullable_string(arguments, "medication_notes"),
        },
        "SeverityScale": {
            "Mild": "1-3: noticeable; normal activity mostly possible",
            "Moderate": "4-6: interferes with some activity",
            "Severe": "7-9: normal activity difficult",
            "Worst": "10: worst imaginable",
        },
    }


CHECKIN_OUTPUT_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
        "Draft": {
            "type": "object",
            "properties": {
                "idempotency_key": {"type": "string"},
                "record_type": {"type": "string", "enum": ["headache", "medication"]},
                "date": {"type": "string"},
                "timezone": {"type": "string"},
                "onset_at": {"type": ["string", "null"]},
                "severity": {"type": ["integer", "null"], "minimum": 1, "maximum": 10},
                "location": {"type": ["string", "null"]},
                "notes": {"type": ["string", "null"]},
                "medication_name": {"type": ["string", "null"]},
                "medication_dose": {"type": ["string", "null"]},
                "medication_taken_at": {"type": ["string", "null"]},
                "medication_notes": {"type": ["string", "null"]},
            },
            "required": [
                "idempotency_key",
                "record_type",
                "date",
                "timezone",
                "onset_at",
                "severity",
                "location",
                "notes",
                "medication_name",
                "medication_dose",
                "medication_taken_at",
                "medication_notes",
            ],
            "additionalProperties": False,
        },
        "SeverityScale": {
            "type": "object",
            "properties": {
                "Mild": {"type": "string"},
                "Moderate": {"type": "string"},
                "Severe": {"type": "string"},
                "Worst": {"type": "string"},
            },
            "required": ["Mild", "Moderate", "Severe", "Worst"],
            "additionalProperties": False,
        },
        "TaskAwareness": {"type": "object"},
        "AgentNotice": {"type": "string"},
    },
    "required": ["Draft", "SeverityScale"],
    "additionalProperties": True,
}


HEALTH_ACTIONS_OUTPUT_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
        "LinkedToday": {"type": "string"},
        "ReminderTimeZone": {"type": "string"},
        "TaskAwareness": {"type": "object"},
        "AgentNotice": {"type": "string"},
    },
    "required": ["LinkedToday", "ReminderTimeZone"],
    "additionalProperties": True,
}


APP_TOOLS: dict[str, dict[str, Any]] = {
    "app_complete_health_actions": {
        "description": (
            "MCP App: show an interactive completion form for genuinely outstanding Health items. "
            "Call this immediately when any Health tool result contains "
            "TaskAwareness.ActionApp.Required=true. Do not call it when ActionApp is absent."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        "outputSchema": HEALTH_ACTIONS_OUTPUT_SCHEMA,
        "annotations": {
            "title": "App: Complete health items",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "_meta": _app_tool_meta(
            HEALTH_ACTIONS_RESOURCE_URI,
            invoking="Checking outstanding health items…",
            invoked="Health items are ready.",
        ),
        "handler": _app_complete_health_actions,
    },
    "app_show_today_health": {
        "description": (
            "MCP App: display the linked Everday user's daily health summary as a compact interactive card. "
            "Use this when the user asks to see today's health status or a visual daily overview."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "date": {
                    "type": "string",
                    "description": "Optional YYYY-MM-DD. Defaults to today in the linked user's timezone.",
                }
            },
            "additionalProperties": False,
        },
        "outputSchema": OUTPUT_SCHEMAS["get_today_summary"],
        "annotations": {
            "title": "App: Today's health",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "_meta": _app_tool_meta(
            TODAY_RESOURCE_URI,
            invoking="Loading today's health…",
            invoked="Today's health is ready.",
        ),
        "handler": _app_show_today_health,
    },
    "app_show_food_log": {
        "description": (
            "MCP App: display the linked Everday user's food log for one day as an interactive table. "
            "Use this when the user explicitly asks to see or review their food log. Meal logging, "
            "update, and delete tools render this card automatically, so do not call this again after "
            "a successful meal mutation unless the card did not render or the user asks to refresh it."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "date": {
                    "type": "string",
                    "description": "Optional YYYY-MM-DD. Defaults to today in the linked user's timezone.",
                }
            },
            "additionalProperties": False,
        },
        "outputSchema": OUTPUT_SCHEMAS["get_today_summary"],
        "annotations": {
            "title": "App: Food log",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "_meta": _app_tool_meta(
            FOOD_LOG_RESOURCE_URI,
            invoking="Loading food log…",
            invoked="Food log is ready.",
        ),
        "handler": _app_show_food_log,
    },
    "app_prepare_health_checkin": {
        "description": (
            "MCP App: present an editable confirmation card before saving a headache with an optional linked "
            "medication dose, or a standalone medication dose. Use this instead of writing immediately "
            "when ChatGPT supports MCP Apps. Preserve only details the user actually provided; the user "
            "will review the draft and activate Save in the card."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "record_type": {
                    "type": "string",
                    "enum": ["headache", "medication"],
                    "description": "Use headache when a headache is being recorded; medication for a standalone dose.",
                },
                "idempotency_key": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 200,
                    "description": "Optional stable retry key. The server creates one when omitted.",
                },
                "date": {"type": "string", "description": "YYYY-MM-DD in the linked user's timezone."},
                "onset_at": {"type": "string", "description": "Exact ISO 8601 headache onset time, only when stated."},
                "severity": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 10,
                    "description": (
                        "Self-reported severity. Preserve an exact number; when only a label is stated, "
                        "prefill mild=2, moderate=5, or severe=8 for the user to review. Leave absent "
                        "when no intensity was stated."
                    ),
                },
                "location": {"type": "string", "maxLength": 200},
                "notes": {"type": "string"},
                "medication_name": {"type": "string", "maxLength": 200},
                "medication_dose": {
                    "type": "string",
                    "maxLength": 120,
                    "description": "User-stated dose such as '2 tablets'. Never infer strength or milligrams.",
                },
                "medication_taken_at": {
                    "type": "string",
                    "description": "Exact ISO 8601 time taken, only when stated.",
                },
                "medication_notes": {"type": "string"},
            },
            "additionalProperties": False,
        },
        "outputSchema": CHECKIN_OUTPUT_SCHEMA,
        "annotations": {
            "title": "App: Health check-in",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "_meta": _app_tool_meta(
            CHECKIN_RESOURCE_URI,
            invoking="Preparing health check-in…",
            invoked="Health check-in is ready to review.",
        ),
        "handler": _app_prepare_health_checkin,
    },
}

APP_OUTPUT_SCHEMAS = {
    name: spec["outputSchema"]
    for name, spec in APP_TOOLS.items()
}


RESOURCE_DESCRIPTIONS = {
    TODAY_RESOURCE_URI: "Compact daily health summary with nutrition, activity, and recorded measurements.",
    CHECKIN_RESOURCE_URI: "Editable headache and medication check-in with explicit save confirmation.",
    FOOD_LOG_RESOURCE_URI: "Daily food log with meal entries, quantities, nutrition totals, and targets.",
    HEALTH_ACTIONS_RESOURCE_URI: "Conditional forms for outstanding daily health details, reviews, measurements, and tasks.",
}


FOOD_LOG_MUTATION_TOOLS = frozenset(
    {"log_meal_text", "log_meal_image", "log_meal_manual", "update_meal", "delete_meal"}
)
ACTION_FORM_EMBEDDED_TOOLS = FOOD_LOG_MUTATION_TOOLS | {
    "app_show_today_health",
    "app_show_food_log",
}


def tool_meta(name: str, configured: dict[str, Any] | None) -> dict[str, Any] | None:
    if configured is not None:
        return configured
    if name in FOOD_LOG_MUTATION_TOOLS:
        return _app_tool_meta(
            FOOD_LOG_RESOURCE_URI,
            invoking="Updating food log…",
            invoked="Food log updated.",
        )
    return None


def resources() -> list[types.Resource]:
    return [
        types.Resource(
            name="Health Actions",
            title="Outstanding health actions",
            uri=HEALTH_ACTIONS_RESOURCE_URI,
            description=RESOURCE_DESCRIPTIONS[HEALTH_ACTIONS_RESOURCE_URI],
            mimeType=RESOURCE_MIME_TYPE,
        ),
        types.Resource(
            name="Health Today",
            title="Health today card",
            uri=TODAY_RESOURCE_URI,
            description=RESOURCE_DESCRIPTIONS[TODAY_RESOURCE_URI],
            mimeType=RESOURCE_MIME_TYPE,
        ),
        types.Resource(
            name="Health Check-in",
            title="Health symptom and medication check-in",
            uri=CHECKIN_RESOURCE_URI,
            description=RESOURCE_DESCRIPTIONS[CHECKIN_RESOURCE_URI],
            mimeType=RESOURCE_MIME_TYPE,
        ),
        types.Resource(
            name="Health Food Log",
            title="Health food log card",
            uri=FOOD_LOG_RESOURCE_URI,
            description=RESOURCE_DESCRIPTIONS[FOOD_LOG_RESOURCE_URI],
            mimeType=RESOURCE_MIME_TYPE,
        ),
    ]


def read_resource(uri: str) -> types.ReadResourceResult:
    filenames = {
        TODAY_RESOURCE_URI: "today.html",
        CHECKIN_RESOURCE_URI: "checkin.html",
        FOOD_LOG_RESOURCE_URI: "food-log.html",
        HEALTH_ACTIONS_RESOURCE_URI: "health-actions.html",
    }
    filename = filenames.get(uri)
    if filename is None:
        raise ValueError(f"Unknown resource: {uri}")
    html = (_UI_DIST / filename).read_text(encoding="utf-8")
    description = RESOURCE_DESCRIPTIONS[uri]
    return types.ReadResourceResult(
        cacheScope="public",
        ttlMs=86_400_000,
        contents=[
            types.TextResourceContents(
                uri=uri,
                mimeType=RESOURCE_MIME_TYPE,
                text=html,
                _meta={
                    "ui": {
                        "prefersBorder": True,
                        "csp": {"connectDomains": [], "resourceDomains": []},
                    },
                    "openai/widgetDescription": description,
                    "openai/widgetPrefersBorder": True,
                    "openai/widgetCSP": {
                        "connect_domains": [],
                        "resource_domains": [],
                    },
                },
            )
        ],
    )
