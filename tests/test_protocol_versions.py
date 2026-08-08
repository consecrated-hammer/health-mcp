import os
import sys
import unittest
from email.message import Message
from pathlib import Path
from unittest.mock import patch

from cryptography.fernet import Fernet


os.environ.setdefault("HEALTH_MCP_EVERDAY_BASE_URL", "http://everday.test")
os.environ.setdefault("HEALTH_MCP_ENCRYPTION_KEY", Fernet.generate_key().decode("utf-8"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app  # noqa: E402


MODERN = app.MODERN_PROTOCOL_VERSION
LEGACY = app.LEGACY_PROTOCOL_VERSION


def _headers(**values: str) -> Message:
    """Builds a case-insensitive header container matching http.server's."""
    message = Message()
    for key, value in values.items():
        message[key.replace("_", "-")] = value
    return message


def _meta(version: str = MODERN, *, capabilities: dict | None = {}) -> dict:
    meta = {app.META_PROTOCOL_VERSION: version}
    if capabilities is not None:
        meta[app.META_CLIENT_CAPABILITIES] = capabilities
    meta[app.META_CLIENT_INFO] = {"name": "TestClient", "version": "1.0.0"}
    return meta


def _modern_request(method: str, params: dict | None = None, request_id: str = "r1") -> dict:
    body = dict(params or {})
    body.setdefault("_meta", _meta())
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": body}


class EraDetectionTests(unittest.TestCase):
    def test_legacy_initialize_still_answers_the_legacy_revision(self) -> None:
        status, response = app._handle_mcp_post(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}, _headers()
        )

        self.assertEqual(status, 200)
        self.assertEqual(response["result"]["protocolVersion"], LEGACY)
        self.assertEqual(response["result"]["serverInfo"]["name"], "health-mcp")

    def test_legacy_tools_list_has_no_modern_envelope(self) -> None:
        status, response = app._handle_mcp_post(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}, _headers()
        )

        self.assertEqual(status, 200)
        self.assertNotIn("resultType", response["result"])
        self.assertNotIn("_meta", response["result"])
        self.assertTrue(response["result"]["tools"])

    def test_params_without_protocol_version_route_to_the_legacy_era(self) -> None:
        # A legacy client may still send _meta (for example progressToken).
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/list",
            "params": {"_meta": {"progressToken": "abc"}},
        }

        self.assertFalse(app._is_modern_request(payload))
        status, response = app._handle_mcp_post(payload, _headers())
        self.assertEqual(status, 200)
        self.assertNotIn("resultType", response["result"])

    def test_per_request_protocol_version_selects_the_modern_era(self) -> None:
        self.assertTrue(app._is_modern_request(_modern_request("tools/list")))


class ModernDispatchTests(unittest.TestCase):
    def test_server_discover_advertises_only_selectable_revisions(self) -> None:
        status, response = app._handle_mcp_post(
            _modern_request("server/discover"),
            _headers(MCP_Protocol_Version=MODERN, Mcp_Method="server/discover"),
        )

        self.assertEqual(status, 200)
        result = response["result"]
        self.assertEqual(result["resultType"], "complete")
        # The legacy revision is reachable only via `initialize`, so advertising
        # it here would invite a downgrade the modern path rejects.
        self.assertEqual(result["supportedVersions"], [MODERN])
        self.assertNotIn(LEGACY, result["supportedVersions"])
        self.assertEqual(result["capabilities"], {"tools": {}})
        self.assertEqual(result["_meta"][app.META_SERVER_INFO]["name"], "health-mcp")

    def test_modern_tools_list_carries_the_result_envelope(self) -> None:
        status, response = app._handle_mcp_post(
            _modern_request("tools/list"),
            _headers(MCP_Protocol_Version=MODERN, Mcp_Method="tools/list"),
        )

        self.assertEqual(status, 200)
        self.assertEqual(response["result"]["resultType"], "complete")
        self.assertIn(app.META_SERVER_INFO, response["result"]["_meta"])
        names = {tool["name"] for tool in response["result"]["tools"]}
        self.assertIn("get_today_summary", names)

    def test_modern_tools_call_reaches_the_tool_and_wraps_the_result(self) -> None:
        fake = {
            "description": "Test tool",
            "inputSchema": {"type": "object", "properties": {}},
            "handler": lambda arguments, headers: {"Ok": True},
        }
        with (
            patch.dict(app.TOOLS, {"fake_tool": fake}),
            patch.object(app, "_task_awareness", return_value=None),
        ):
            status, response = app._handle_mcp_post(
                _modern_request("tools/call", {"name": "fake_tool", "arguments": {}}),
                _headers(
                    MCP_Protocol_Version=MODERN,
                    Mcp_Method="tools/call",
                    Mcp_Name="fake_tool",
                ),
            )

        self.assertEqual(status, 200)
        result = response["result"]
        self.assertEqual(result["resultType"], "complete")
        self.assertEqual(result["structuredContent"], {"Ok": True})

    def test_unknown_modern_method_is_404_with_method_not_found(self) -> None:
        status, response = app._handle_mcp_post(
            _modern_request("resources/list"),
            _headers(MCP_Protocol_Version=MODERN, Mcp_Method="resources/list"),
        )

        self.assertEqual(status, 404)
        self.assertEqual(response["error"]["code"], app.ERROR_METHOD_NOT_FOUND)

    def test_modern_notification_is_accepted_without_a_body(self) -> None:
        payload = {"jsonrpc": "2.0", "method": "notifications/x", "params": {"_meta": _meta()}}

        status, response = app._handle_mcp_post(payload, _headers())

        self.assertEqual(status, 202)
        self.assertIsNone(response)


class ModernValidationTests(unittest.TestCase):
    def test_missing_protocol_version_header_is_a_header_mismatch(self) -> None:
        status, response = app._handle_mcp_post(
            _modern_request("tools/list"), _headers(Mcp_Method="tools/list")
        )

        self.assertEqual(status, 400)
        self.assertEqual(response["error"]["code"], app.ERROR_HEADER_MISMATCH)

    def test_protocol_version_header_must_match_the_body(self) -> None:
        status, response = app._handle_mcp_post(
            _modern_request("tools/list"),
            _headers(MCP_Protocol_Version=LEGACY, Mcp_Method="tools/list"),
        )

        self.assertEqual(status, 400)
        self.assertEqual(response["error"]["code"], app.ERROR_HEADER_MISMATCH)

    def test_method_header_must_match_the_body(self) -> None:
        status, response = app._handle_mcp_post(
            _modern_request("tools/list"),
            _headers(MCP_Protocol_Version=MODERN, Mcp_Method="tools/call"),
        )

        self.assertEqual(status, 400)
        self.assertEqual(response["error"]["code"], app.ERROR_HEADER_MISMATCH)

    def test_tools_call_requires_a_matching_name_header(self) -> None:
        status, response = app._handle_mcp_post(
            _modern_request("tools/call", {"name": "get_today_summary", "arguments": {}}),
            _headers(
                MCP_Protocol_Version=MODERN,
                Mcp_Method="tools/call",
                Mcp_Name="log_weight",
            ),
        )

        self.assertEqual(status, 400)
        self.assertEqual(response["error"]["code"], app.ERROR_HEADER_MISMATCH)

    def test_tools_call_rejects_a_missing_name_header(self) -> None:
        status, response = app._handle_mcp_post(
            _modern_request("tools/call", {"name": "get_today_summary", "arguments": {}}),
            _headers(MCP_Protocol_Version=MODERN, Mcp_Method="tools/call"),
        )

        self.assertEqual(status, 400)
        self.assertEqual(response["error"]["code"], app.ERROR_HEADER_MISMATCH)

    def test_unsupported_version_lists_what_the_server_speaks(self) -> None:
        payload = _modern_request("tools/list")
        payload["params"]["_meta"] = _meta("1900-01-01")

        status, response = app._handle_mcp_post(
            payload,
            _headers(MCP_Protocol_Version="1900-01-01", Mcp_Method="tools/list"),
        )

        self.assertEqual(status, 400)
        self.assertEqual(response["error"]["code"], app.ERROR_UNSUPPORTED_PROTOCOL_VERSION)
        self.assertEqual(response["error"]["data"]["supported"], [MODERN])
        self.assertEqual(response["error"]["data"]["requested"], "1900-01-01")

    def test_offered_retry_versions_are_all_actually_accepted(self) -> None:
        """Guards the retry loop: a client retrying with an offered version must
        not be told the same thing again."""
        payload = _modern_request("tools/list")
        payload["params"]["_meta"] = _meta("1900-01-01")
        _, response = app._handle_mcp_post(
            payload, _headers(MCP_Protocol_Version="1900-01-01", Mcp_Method="tools/list")
        )

        for offered in response["error"]["data"]["supported"]:
            retry = _modern_request("tools/list")
            retry["params"]["_meta"] = _meta(offered)
            status, retried = app._handle_mcp_post(
                retry, _headers(MCP_Protocol_Version=offered, Mcp_Method="tools/list")
            )
            self.assertEqual(status, 200, f"retrying with offered version {offered} failed")
            self.assertNotIn("error", retried)

    def test_missing_client_capabilities_is_invalid_params(self) -> None:
        payload = _modern_request("tools/list")
        payload["params"]["_meta"] = _meta(capabilities=None)

        status, response = app._handle_mcp_post(
            payload, _headers(MCP_Protocol_Version=MODERN, Mcp_Method="tools/list")
        )

        self.assertEqual(status, 400)
        self.assertEqual(response["error"]["code"], app.ERROR_INVALID_PARAMS)


class HeaderValueEncodingTests(unittest.TestCase):
    def test_plain_values_pass_through(self) -> None:
        self.assertEqual(app._decode_header_value("get_today_summary"), "get_today_summary")

    def test_base64_sentinel_is_decoded(self) -> None:
        self.assertEqual(app._decode_header_value("=?base64?SGVsbG8sIOS4lueVjA==?="), "Hello, 世界")

    def test_malformed_sentinel_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            app._decode_header_value("=?base64?not valid base64!?=")

    def test_encoded_name_header_is_compared_after_decoding(self) -> None:
        fake = {
            "description": "Test tool",
            "inputSchema": {"type": "object", "properties": {}},
            "handler": lambda arguments, headers: {"Ok": True},
        }
        with (
            patch.dict(app.TOOLS, {"ünïcode_tool": fake}),
            patch.object(app, "_task_awareness", return_value=None),
        ):
            status, response = app._handle_mcp_post(
                _modern_request("tools/call", {"name": "ünïcode_tool", "arguments": {}}),
                _headers(
                    MCP_Protocol_Version=MODERN,
                    Mcp_Method="tools/call",
                    Mcp_Name="=?base64?w7xuw69jb2RlX3Rvb2w=?=",
                ),
            )

        self.assertEqual(status, 200)
        self.assertEqual(response["result"]["structuredContent"], {"Ok": True})


if __name__ == "__main__":
    unittest.main()
