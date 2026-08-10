import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import call, patch

from cryptography.fernet import Fernet


os.environ.setdefault("HEALTH_MCP_EVERDAY_BASE_URL", "http://everday.test")
os.environ.setdefault("HEALTH_MCP_ENCRYPTION_KEY", Fernet.generate_key().decode("utf-8"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app  # noqa: E402


class SymptomTrackingToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.principal = {"subject": "chat-user", "email": "user@example.test", "name": "Test User"}
        self.account = {"everday_user_id": 7}

    def test_log_headache_can_include_exact_linked_medication_dose(self) -> None:
        responses = [
            {"ReminderTimeZone": "Australia/Adelaide"},
            {"ReminderTimeZone": "Australia/Adelaide"},
            {"HeadacheEventId": "headache-id", "EventType": "headache"},
            {"MedicationDoseId": "dose-id", "MedicationName": "Panadol", "Dose": "2 tablets"},
        ]
        arguments = {
            "idempotency_key": "2026-08-10-afternoon-headache",
            "date": "2026-08-10",
            "onset_at": "2026-08-10T15:20:00+09:30",
            "severity": 4,
            "location": "temples",
            "notes": "Started after lunch.",
            "medication_name": "Panadol",
            "medication_dose": "2 tablets",
            "medication_taken_at": "2026-08-10T15:25:00+09:30",
        }
        with (
            patch.object(app, "_require_principal", return_value=self.principal),
            patch.object(app, "_refresh_access_for_principal", return_value=("access-token", self.account)),
            patch.object(app, "_http_json", side_effect=responses) as request,
        ):
            result = app._tool_log_headache(arguments, {})

        self.assertEqual(result["MedicationDose"]["Dose"], "2 tablets")
        headache_payload = request.call_args_list[2].kwargs["payload"]
        medication_payload = request.call_args_list[3].kwargs["payload"]
        self.assertEqual(headache_payload["EventType"], "headache")
        self.assertEqual(headache_payload["Severity"], 4)
        self.assertEqual(medication_payload["Dose"], "2 tablets")
        self.assertNotIn("Strength", medication_payload)
        self.assertEqual(medication_payload["HeadacheEventId"], headache_payload["HeadacheEventId"])

    def test_idempotency_key_produces_stable_record_ids(self) -> None:
        arguments = {
            "idempotency_key": "same-real-world-event",
            "date": "2026-08-10",
            "medication_name": "Panadol",
            "medication_dose": "2 tablets",
        }
        payloads = []

        def capture_request(_method, path, *, payload=None, headers=None):
            payloads.append((path, payload))
            return payload

        with (
            patch.object(app, "_require_principal", return_value=self.principal),
            patch.object(app, "_refresh_access_for_principal", return_value=("access-token", self.account)),
            patch.object(app, "_http_json", side_effect=capture_request),
        ):
            app._tool_log_headache(arguments, {})
            app._tool_log_headache(arguments, {})

        self.assertEqual(payloads[0][1]["HeadacheEventId"], payloads[2][1]["HeadacheEventId"])
        self.assertEqual(payloads[1][1]["MedicationDoseId"], payloads[3][1]["MedicationDoseId"])
        self.assertNotEqual(payloads[0][1]["HeadacheEventId"], payloads[1][1]["MedicationDoseId"])

    def test_medication_details_require_a_name_before_writing(self) -> None:
        with (
            patch.object(app, "_require_principal", return_value=self.principal),
            patch.object(app, "_refresh_access_for_principal", return_value=("access-token", self.account)),
            patch.object(app, "_http_json") as request,
        ):
            with self.assertRaisesRegex(ValueError, "medication_name is required"):
                app._tool_log_headache(
                    {
                        "idempotency_key": "missing-name",
                        "date": "2026-08-10",
                        "medication_dose": "2 tablets",
                    },
                    {},
                )

        request.assert_not_called()

    def test_linked_medication_time_error_names_the_public_field(self) -> None:
        with (
            patch.object(app, "_require_principal", return_value=self.principal),
            patch.object(app, "_refresh_access_for_principal", return_value=("access-token", self.account)),
            patch.object(app, "_http_json") as request,
        ):
            with self.assertRaisesRegex(ValueError, "medication_taken_at must be an ISO 8601 datetime"):
                app._tool_log_headache(
                    {
                        "idempotency_key": "invalid-medication-time",
                        "date": "2026-08-10",
                        "medication_name": "Panadol",
                        "medication_taken_at": "tomorrow afternoon",
                    },
                    {},
                )

        request.assert_not_called()

    def test_explicit_date_must_match_timestamp_in_linked_timezone(self) -> None:
        with (
            patch.object(app, "_require_principal", return_value=self.principal),
            patch.object(app, "_refresh_access_for_principal", return_value=("access-token", self.account)),
            patch.object(app, "_http_json", return_value={"ReminderTimeZone": "Australia/Adelaide"}) as request,
        ):
            with self.assertRaisesRegex(ValueError, "date must match onset_at"):
                app._tool_log_headache(
                    {
                        "idempotency_key": "mismatched-date",
                        "date": "2026-08-10",
                        "onset_at": "2026-08-10T15:00:00Z",
                    },
                    {},
                )

        request.assert_called_once_with(
            "GET",
            "/api/integrations/health-mcp/context",
            headers={"Authorization": "Bearer access-token"},
        )

    def test_utc_timestamp_is_grouped_by_linked_users_local_date(self) -> None:
        with (
            patch.object(app, "_require_principal", return_value=self.principal),
            patch.object(app, "_refresh_access_for_principal", return_value=("access-token", self.account)),
            patch.object(
                app,
                "_http_json",
                side_effect=[{"ReminderTimeZone": "Australia/Adelaide"}, {"HeadacheEventId": "headache-id"}],
            ) as request,
        ):
            app._tool_log_headache(
                {
                    "idempotency_key": "utc-near-midnight",
                    "onset_at": "2026-08-10T15:00:00Z",
                },
                {},
            )

        payload = request.call_args_list[1].kwargs["payload"]
        self.assertEqual(payload["LogDate"], "2026-08-11")

    def test_missing_date_and_time_use_linked_users_timezone(self) -> None:
        now = datetime(2026, 8, 10, 15, 45, tzinfo=timezone.utc)
        with (
            patch.object(app, "_require_principal", return_value=self.principal),
            patch.object(app, "_refresh_access_for_principal", return_value=("access-token", self.account)),
            patch.object(app, "_utc_now", return_value=now),
            patch.object(
                app,
                "_http_json",
                side_effect=[{"ReminderTimeZone": "Australia/Adelaide"}, {"MedicationDoseId": "dose-id"}],
            ) as request,
        ):
            app._tool_log_medication_dose(
                {
                    "idempotency_key": "evening-dose",
                    "medication_name": "Panadol",
                    "dose": "2 tablets",
                },
                {},
            )

        payload = request.call_args_list[1].kwargs["payload"]
        self.assertEqual(payload["LogDate"], "2026-08-11")
        self.assertEqual(payload["TakenAt"], "2026-08-11T01:15:00+09:30")

    def test_history_tools_forward_optional_date_filters(self) -> None:
        with (
            patch.object(app, "_require_principal", return_value=self.principal),
            patch.object(app, "_refresh_access_for_principal", return_value=("access-token", self.account)),
            patch.object(app, "_http_json", side_effect=[[{"HeadacheEventId": "h1"}], [{"MedicationDoseId": "m1"}]]) as request,
        ):
            headaches = app._tool_get_headaches({"date": "2026-08-10"}, {})
            medications = app._tool_get_medication_doses({}, {})

        self.assertEqual(headaches, {"Items": [{"HeadacheEventId": "h1"}]})
        self.assertEqual(medications, {"Items": [{"MedicationDoseId": "m1"}]})
        self.assertEqual(
            request.call_args_list,
            [
                call(
                    "GET",
                    "/api/integrations/health-mcp/headaches?log_date=2026-08-10",
                    headers={"Authorization": "Bearer access-token"},
                ),
                call(
                    "GET",
                    "/api/integrations/health-mcp/medication-doses",
                    headers={"Authorization": "Bearer access-token"},
                ),
            ],
        )

    def test_tool_contract_marks_writes_idempotent_and_reads_read_only(self) -> None:
        self.assertTrue(app._tool_annotations("log_headache")["idempotentHint"])
        self.assertTrue(app._tool_annotations("log_medication_dose")["idempotentHint"])
        self.assertTrue(app._tool_annotations("get_headaches")["readOnlyHint"])
        self.assertTrue(app._tool_annotations("get_medication_doses")["readOnlyHint"])


if __name__ == "__main__":
    unittest.main()
