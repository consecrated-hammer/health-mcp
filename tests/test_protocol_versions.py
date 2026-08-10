import hashlib
import json
import os
import sys
import unittest
from email.message import Message
from importlib.metadata import version
from pathlib import Path
from unittest.mock import patch

import anyio
from cryptography.fernet import Fernet
from mcp import Client


os.environ.setdefault("HEALTH_MCP_EVERDAY_BASE_URL", "http://everday.test")
os.environ.setdefault("HEALTH_MCP_ENCRYPTION_KEY", Fernet.generate_key().decode("utf-8"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app as health  # noqa: E402
import server  # noqa: E402


EXPECTED_TOOL_CONTRACT_SHA256 = "50d6a542309374a6afab393d781675a8060277bbc881230cb7a19cd23dc266d9"


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
            "annotations": tool.annotations.model_dump(by_alias=True, exclude_none=True),
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
        self.assertEqual(len(tools), 62)
        payload = _contract_payload(tools)
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        self.assertEqual(hashlib.sha256(encoded).hexdigest(), EXPECTED_TOOL_CONTRACT_SHA256)

    def test_legacy_client_remains_supported_by_the_sdk(self) -> None:
        async def exercise() -> tuple[str | None, int]:
            async with Client(server.mcp_server, mode="legacy") as client:
                listed = await client.list_tools()
                return client.protocol_version, len(listed.tools)

        protocol_version, tool_count = anyio.run(exercise)

        self.assertEqual(protocol_version, "2025-11-25")
        self.assertEqual(tool_count, 62)

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


if __name__ == "__main__":
    unittest.main()
