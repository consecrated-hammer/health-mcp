import hashlib
import json
import os
import sys
import unittest
from datetime import datetime, timezone
from email.message import Message
from importlib.metadata import version
from pathlib import Path
from unittest.mock import ANY, patch

import anyio
from cryptography.fernet import Fernet
from jsonschema import Draft202012Validator, ValidationError, validate
from mcp import Client


os.environ.setdefault("HEALTH_MCP_EVERDAY_BASE_URL", "http://everday.test")
os.environ.setdefault("HEALTH_MCP_ENCRYPTION_KEY", Fernet.generate_key().decode("utf-8"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app as health  # noqa: E402
import mcp_apps  # noqa: E402
from output_schemas import OUTPUT_SCHEMAS  # noqa: E402
import server  # noqa: E402


EXPECTED_TOOL_CONTRACT_SHA256 = "f4a05a1f42508aa8094cfa524af5378cee3d29123094c98b99620ebf2d491941"


def _headers(**values: str) -> Message:
    message = Message()
    for key, value in values.items():
        message[key.replace("_", "-")] = value
    return message


def _contract_payload(tools: list) -> list[dict]:
    return [
        {
            "name": tool.name,
            "description": tool.description,
            "inputSchema": tool.input_schema,
            "outputSchema": tool.output_schema,
            "annotations": tool.annotations.model_dump(by_alias=True, exclude_none=True),
            "meta": tool.meta,
        }
        for tool in tools
    ]


class SDKMigrationTests(unittest.TestCase):
    def test_runtime_is_pinned_to_python_sdk_v2(self) -> None:
        self.assertEqual(version("mcp"), "2.0.0")

    def test_auto_client_uses_the_modern_protocol_and_preserves_tool_contract(self) -> None:
        async def exercise() -> tuple[str | None, list]:
            async with Client(server.mcp_server, mode="auto") as client:
                listed = await client.list_tools()
                return client.protocol_version, listed.tools

        protocol_version, tools = anyio.run(exercise)

        self.assertEqual(protocol_version, "2026-07-28")
        self.assertEqual(len(tools), 65)
        payload = _contract_payload(tools)
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        self.assertEqual(hashlib.sha256(encoded).hexdigest(), EXPECTED_TOOL_CONTRACT_SHA256)

    def test_legacy_client_remains_supported_by_the_sdk(self) -> None:
        async def exercise() -> tuple[str | None, list]:
            async with Client(server.mcp_server, mode="legacy") as client:
                listed = await client.list_tools()
                return client.protocol_version, listed.tools

        protocol_version, tools = anyio.run(exercise)

        self.assertEqual(protocol_version, "2025-11-25")
        self.assertEqual(len(tools), 65)
        self.assertTrue(all(tool.output_schema is not None for tool in tools))

    def test_every_tool_declares_a_valid_output_schema(self) -> None:
        self.assertEqual(set(OUTPUT_SCHEMAS), set(health.TOOLS))
        for name, schema in OUTPUT_SCHEMAS.items():
            with self.subTest(tool=name):
                Draft202012Validator.check_schema(schema)
                self.assertEqual(schema["type"], "object")
                self.assertIn("TaskAwareness", schema["properties"])
                self.assertIn("AgentNotice", schema["properties"])

        all_output_schemas = {**OUTPUT_SCHEMAS, **mcp_apps.APP_OUTPUT_SCHEMAS}
        descriptors = server._tool_descriptors()
        self.assertTrue(all(tool.output_schema == all_output_schemas[tool.name] for tool in descriptors))

    def test_app_tools_expose_versioned_ui_metadata_and_resources(self) -> None:
        descriptors = {tool.name: tool for tool in server._tool_descriptors()}
        for name, uri in {
            "show_today_health": mcp_apps.TODAY_RESOURCE_URI,
            "show_food_log": mcp_apps.FOOD_LOG_RESOURCE_URI,
            "prepare_health_checkin": mcp_apps.CHECKIN_RESOURCE_URI,
        }.items():
            with self.subTest(tool=name):
                meta = descriptors[name].meta or {}
                self.assertEqual(meta["ui"]["resourceUri"], uri)
                self.assertEqual(meta["ui"]["visibility"], ["model", "app"])
                self.assertEqual(meta["openai/outputTemplate"], uri)

        async def exercise() -> tuple[list, dict[str, tuple[str, dict]]]:
            async with Client(server.mcp_server, mode="auto") as client:
                listed = await client.list_resources()
                resource_content: dict[str, tuple[str, dict]] = {}
                for uri in mcp_apps.RESOURCE_DESCRIPTIONS:
                    read = await client.read_resource(uri)
                    content = read.contents[0]
                    resource_content[uri] = (content.text, content.meta or {})
                return listed.resources, resource_content

        resources, resource_content = anyio.run(exercise)
        self.assertEqual({str(item.uri) for item in resources}, set(mcp_apps.RESOURCE_DESCRIPTIONS))
        for uri, app_name in {
            mcp_apps.TODAY_RESOURCE_URI: "Health Today",
            mcp_apps.CHECKIN_RESOURCE_URI: "Health Check-in",
            mcp_apps.FOOD_LOG_RESOURCE_URI: "Health Food Log",
        }.items():
            with self.subTest(resource=uri):
                html, meta = resource_content[uri]
                self.assertIn("<main id=\"app\"", html)
                self.assertIn(app_name, html)
                self.assertEqual(meta["ui"]["csp"], {"connectDomains": [], "resourceDomains": []})

    def test_meal_mutations_render_the_food_log_without_a_follow_up_call(self) -> None:
        descriptors = {tool.name: tool for tool in server._tool_descriptors()}
        for name in mcp_apps.FOOD_LOG_MUTATION_TOOLS:
            with self.subTest(tool=name):
                meta = descriptors[name].meta or {}
                self.assertEqual(meta["ui"]["resourceUri"], mcp_apps.FOOD_LOG_RESOURCE_URI)
                self.assertEqual(meta["openai/outputTemplate"], mcp_apps.FOOD_LOG_RESOURCE_URI)

    def test_app_handlers_use_authoritative_health_tools_without_writing_drafts(self) -> None:
        summary = {
            "Workouts": [],
            "Entries": [],
            "Totals": {},
            "Summary": {"LogDate": "2026-08-11"},
            "Targets": {},
        }
        with (
            patch.object(
                health,
                "_tool_get_connection_context",
                return_value={"ReminderTimeZone": "Australia/Adelaide"},
            ),
            patch.object(
                health,
                "_utc_now",
                return_value=datetime(2026, 8, 10, 15, 0, tzinfo=timezone.utc),
            ),
            patch.object(health, "_tool_get_today_summary", return_value=summary) as get_summary,
        ):
            result = mcp_apps.APP_TOOLS["show_today_health"]["handler"]({}, _headers())
        self.assertEqual(result, summary)
        get_summary.assert_called_once_with({"date": "2026-08-11"}, ANY)

        food_log = {
            "Workouts": [],
            "Entries": [],
            "Totals": {},
            "Summary": {"LogDate": "2026-08-11"},
            "Targets": {},
        }
        with (
            patch.object(
                health,
                "_tool_get_connection_context",
                return_value={"ReminderTimeZone": "Australia/Adelaide"},
            ),
            patch.object(
                health,
                "_utc_now",
                return_value=datetime(2026, 8, 10, 15, 0, tzinfo=timezone.utc),
            ),
            patch.object(health, "_tool_get_today_summary", return_value=food_log) as get_food_log,
        ):
            result = mcp_apps.APP_TOOLS["show_food_log"]["handler"]({}, _headers())
        self.assertEqual(result, food_log)
        get_food_log.assert_called_once_with({"date": "2026-08-11"}, ANY)

        with patch.object(
            health,
            "_tool_get_connection_context",
            return_value={"ReminderTimeZone": "Australia/Adelaide"},
        ), patch.object(
            health,
            "_utc_now",
            return_value=datetime(2026, 8, 10, 15, 0, tzinfo=timezone.utc),
        ):
            draft = mcp_apps.APP_TOOLS["prepare_health_checkin"]["handler"](
                {"record_type": "headache", "medication_name": "Panadol", "medication_dose": "2 tablets"},
                _headers(),
            )
        validate(draft, mcp_apps.CHECKIN_OUTPUT_SCHEMA)
        self.assertEqual(draft["Draft"]["date"], "2026-08-11")
        self.assertEqual(draft["Draft"]["timezone"], "Australia/Adelaide")
        self.assertIsNone(draft["Draft"]["severity"])
        self.assertEqual(draft["Draft"]["medication_dose"], "2 tablets")
        self.assertTrue(draft["Draft"]["idempotency_key"].startswith("health-checkin-"))

    def test_output_schema_validates_stable_fields_and_optional_awareness(self) -> None:
        result = {
            "Headache": {
                "HeadacheEventId": "headache-id",
                "LogDate": "2026-08-11",
                "EventType": "headache",
            },
            "MedicationDose": None,
            "TaskAwareness": {"AgentNotice": "A task is due."},
            "AgentNotice": "A task is due.",
        }

        validate(result, OUTPUT_SCHEMAS["log_headache"])
        with self.assertRaises(ValidationError):
            validate({"MedicationDose": None}, OUTPUT_SCHEMAS["log_headache"])

    def test_client_capability_log_contains_only_protocol_metadata(self) -> None:
        async def exercise() -> None:
            async with Client(server.mcp_server, mode="auto") as client:
                await client.list_tools()

        with self.assertLogs("uvicorn.error", level="INFO") as captured:
            anyio.run(exercise)

        entry = next(line for line in captured.output if "mcp_client_capabilities" in line)
        payload = json.loads(entry.split("mcp_client_capabilities ", 1)[1])
        self.assertEqual(payload["event"], "mcp_client_capabilities")
        self.assertEqual(payload["protocol_version"], "2026-07-28")
        self.assertEqual(
            set(payload),
            {"event", "protocol_version", "client_name", "client_version", "capabilities", "extensions"},
        )

    def test_public_and_mcp_routes_are_composed_once(self) -> None:
        paths = [route.path for route in server.app.routes]

        self.assertEqual(paths.count("/mcp"), 1)
        self.assertEqual(paths.count("/healthz"), 1)
        self.assertEqual(paths.count("/version"), 1)
        self.assertEqual(paths.count("/"), 1)
        self.assertEqual(paths.count("/link/{session_token:path}"), 1)

    def test_sdk_transport_uses_the_deployed_bind_host(self) -> None:
        self.assertEqual(server.mcp_server._session_manager.security_settings, None)

    def test_configured_origin_allowlist_still_rejects_unknown_origins(self) -> None:
        async def exercise() -> list[dict]:
            sent: list[dict] = []

            async def receive() -> dict:
                return {"type": "http.request", "body": b"", "more_body": False}

            async def send(message: dict) -> None:
                sent.append(message)

            scope = {
                "type": "http",
                "http_version": "1.1",
                "method": "POST",
                "scheme": "https",
                "path": "/mcp",
                "raw_path": b"/mcp",
                "query_string": b"",
                "headers": [(b"origin", b"https://unexpected.example")],
                "client": ("127.0.0.1", 1234),
                "server": ("health-mcp", 8766),
            }
            await server.app(scope, receive, send)
            return sent

        with patch.object(health.Config, "allowed_origins", frozenset({"https://allowed.example"})):
            sent = anyio.run(exercise)

        self.assertEqual(sent[0]["status"], 403)
        self.assertIn(b"forbidden_origin", sent[1]["body"])

    def test_gateway_identity_headers_reach_existing_tool_handlers(self) -> None:
        with patch.object(health, "_task_awareness", return_value=None):
            result, is_error = server._invoke_tool(
                "connection_status",
                {},
                _headers(
                    X_Auth_Request_Sub="contract-user",
                    X_Auth_Request_Email="contract@example.test",
                ),
            )

        self.assertFalse(is_error)
        self.assertEqual(result["external_subject"], "contract-user")
        self.assertEqual(result["external_email"], "contract@example.test")

    def test_tool_errors_remain_model_visible_call_errors(self) -> None:
        result, is_error = server._invoke_tool("not-a-tool", {}, _headers())

        self.assertTrue(is_error)
        self.assertEqual(result, {"error": "Unknown tool: not-a-tool"})

    def test_sdk_tool_call_returns_structured_error_content(self) -> None:
        async def exercise():
            async with Client(server.mcp_server, mode="auto") as client:
                return await client.call_tool("not-a-tool", {})

        result = anyio.run(exercise)

        self.assertTrue(result.is_error)
        self.assertEqual(result.structured_content, {"error": "Unknown tool: not-a-tool"})


if __name__ == "__main__":
    unittest.main()
