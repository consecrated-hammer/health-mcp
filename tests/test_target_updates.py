import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import call, patch

from cryptography.fernet import Fernet


os.environ.setdefault("HEALTH_MCP_EVERDAY_BASE_URL", "http://everday.test")
os.environ.setdefault("HEALTH_MCP_ENCRYPTION_KEY", Fernet.generate_key().decode("utf-8"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app  # noqa: E402


class UpdateTargetsToolTests(unittest.TestCase):
    def test_get_goals_returns_current_targets_and_active_goal(self) -> None:
        settings = {
            "Targets": {"DailyCalorieTarget": 1500, "StepTarget": 8500},
            "Goal": {"GoalType": "lose", "BmiMin": 20.0, "BmiMax": 25.0, "TargetWeightKg": 70.0},
        }
        with (
            patch.object(app, "_require_principal", return_value={"subject": "test-subject"}),
            patch.object(app, "_refresh_access_for_principal", return_value=("access-token", {})),
            patch.object(app, "_http_json", return_value=settings) as request,
        ):
            result = app._tool_get_goals({}, {})

        self.assertEqual(result, {"Targets": settings["Targets"], "Goal": settings["Goal"]})
        request.assert_called_once_with(
            "GET",
            "/api/health/settings",
            headers={"Authorization": "Bearer access-token"},
        )

    def test_forwards_only_requested_target_fields(self) -> None:
        with (
            patch.object(app, "_require_principal", return_value={"subject": "test-subject"}),
            patch.object(app, "_refresh_access_for_principal", return_value=("access-token", {})),
            patch.object(
                app,
                "_http_json",
                return_value={"Targets": {"DailyCalorieTarget": 1500, "ProteinTargetMin": 95.0}},
            ) as request,
        ):
            result = app._tool_update_targets(
                {"daily_calorie_target": 1500, "protein_target_min": 95.0},
                {},
            )

        self.assertEqual(result, {"Targets": {"DailyCalorieTarget": 1500, "ProteinTargetMin": 95.0}})
        request.assert_called_once_with(
            "PUT",
            "/api/health/settings",
            payload={"DailyCalorieTarget": 1500, "ProteinTargetMin": 95.0},
            headers={"Authorization": "Bearer access-token"},
        )

    def test_requires_at_least_one_target(self) -> None:
        with (
            patch.object(app, "_require_principal", return_value={"subject": "test-subject"}),
            patch.object(app, "_refresh_access_for_principal", return_value=("access-token", {})),
        ):
            with self.assertRaisesRegex(ValueError, "at least one target"):
                app._tool_update_targets({}, {})

    def test_is_registered_as_an_idempotent_write_tool(self) -> None:
        self.assertIn("update_targets", app.TOOLS)
        self.assertIn("update_targets", app.IDEMPOTENT_WRITE_TOOLS)
        self.assertFalse(app._tool_annotations("update_targets")["readOnlyHint"])
        self.assertTrue(app._tool_annotations("update_targets")["idempotentHint"])

    def test_set_goal_applies_goal_with_optional_overrides(self) -> None:
        response = {
            "Goal": {"GoalType": "lose", "BmiMin": 20.0, "BmiMax": 25.0, "TargetWeightKg": 70.0},
            "Targets": {"DailyCalorieTarget": 1600, "ProteinTargetMin": 100.0, "StepTarget": 7000},
        }
        with (
            patch.object(app, "_require_principal", return_value={"subject": "test-subject"}),
            patch.object(app, "_refresh_access_for_principal", return_value=("access-token", {})),
            patch.object(app, "_http_json", return_value=response) as request,
        ):
            result = app._tool_set_goal(
                {
                    "goal_type": "lose",
                    "bmi_min": 20.0,
                    "bmi_max": 25.0,
                    "duration_months": 9,
                    "target_weight_kg": 70.0,
                },
                {},
            )

        self.assertEqual(result["Goal"], response["Goal"])
        self.assertEqual(result["Targets"], response["Targets"])
        request.assert_called_once_with(
            "POST",
            "/api/health/settings/goal",
            payload={
                "GoalType": "lose",
                "BmiMin": 20.0,
                "BmiMax": 25.0,
                "DurationMonths": 9,
                "TargetWeightKgOverride": 70.0,
            },
            headers={"Authorization": "Bearer access-token"},
        )

    def test_set_goal_is_a_non_idempotent_write_tool(self) -> None:
        self.assertIn("set_goal", app.TOOLS)
        self.assertNotIn("set_goal", app.READ_ONLY_TOOLS)
        self.assertNotIn("set_goal", app.IDEMPOTENT_WRITE_TOOLS)
        self.assertFalse(app._tool_annotations("set_goal")["readOnlyHint"])
        self.assertNotIn("idempotentHint", app._tool_annotations("set_goal"))

    def test_preview_goal_recommendation_does_not_apply_goal(self) -> None:
        response = {"Goal": {"GoalType": "lose"}, "DailyCalorieTarget": 1798}
        with (
            patch.object(app, "_require_principal", return_value={"subject": "test-subject"}),
            patch.object(app, "_refresh_access_for_principal", return_value=("access-token", {})),
            patch.object(app, "_http_json", return_value=response) as request,
        ):
            result = app._tool_preview_goal_recommendation(
                {"goal_type": "lose", "bmi_min": 20.0, "bmi_max": 25.0},
                {},
            )

        self.assertEqual(result, response)
        request.assert_called_once_with(
            "POST",
            "/api/health/settings/ai-recommendations",
            payload={"GoalType": "lose", "BmiMin": 20.0, "BmiMax": 25.0},
            headers={"Authorization": "Bearer access-token"},
        )


class TaskAwarenessTests(unittest.TestCase):
    def test_write_tool_response_includes_task_awareness_notice(self) -> None:
        original = app.TOOLS["log_weight"]
        app.TOOLS["log_weight"] = {**original, "handler": lambda _arguments, _headers: {"Logged": True}}
        try:
            with patch.object(
                app,
                "_task_awareness",
                return_value={
                    "Overdue": [{"Title": "Put water at desk", "DueTime": "08:30"}],
                    "Upcoming": [],
                    "AgentNotice": "Overdue health tasks: Put water at desk (due 08:30)",
                },
            ):
                response = app._handle_jsonrpc(
                    {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "log_weight", "arguments": {}}},
                    {},
                )
        finally:
            app.TOOLS["log_weight"] = original

        content = response["result"]["structuredContent"]
        self.assertEqual(content["TaskAwareness"]["Overdue"][0]["Title"], "Put water at desk")
        self.assertIn("Put water at desk", content["AgentNotice"])

    def test_includes_multiple_overdue_and_upcoming_health_tasks(self) -> None:
        now = datetime(2026, 7, 20, 9, 0, tzinfo=timezone.utc)
        tasks = [
            {
                "Id": 1,
                "Title": "Put water at desk",
                "OwnerUserId": 7,
                "RelatedModule": "health",
                "StartDate": "2026-07-20",
                "StartTime": "08:30",
                "RepeatType": "weekly",
                "RepeatInterval": 1,
                "RepeatWeekdays": [0],
                "IsCompleted": False,
            },
            {
                "Id": 2,
                "Title": "Morning walk",
                "OwnerUserId": 7,
                "RelatedModule": "health",
                "StartDate": "2026-07-20",
                "StartTime": "08:45",
                "RepeatType": "none",
                "IsCompleted": False,
            },
            {
                "Id": 3,
                "Title": "Protein bridge",
                "OwnerUserId": 7,
                "RelatedModule": "health",
                "StartDate": "2026-07-20",
                "StartTime": "10:15",
                "RepeatType": "none",
                "IsCompleted": False,
            },
            {
                "Id": 4,
                "Title": "Unrelated task",
                "OwnerUserId": 7,
                "RelatedModule": "chores",
                "StartDate": "2026-07-20",
                "StartTime": "08:00",
                "RepeatType": "none",
                "IsCompleted": False,
            },
        ]

        awareness = app._health_task_items(tasks, 7, "UTC", now)

        self.assertEqual([item["Title"] for item in awareness["Overdue"]], ["Put water at desk", "Morning walk"])
        self.assertEqual([item["Title"] for item in awareness["Upcoming"]], ["Protein bridge"])
        self.assertIn("Put water at desk", awareness["AgentNotice"])
        self.assertIn("Protein bridge", awareness["AgentNotice"])

    def test_flags_weight_when_latest_entry_is_eight_days_old(self) -> None:
        awareness = app._weight_logging_awareness(
            [{"LogDate": "2026-07-12", "WeightKg": 100.0}],
            "UTC",
            datetime(2026, 7, 20, 9, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(awareness["LastLoggedDate"], "2026-07-12")
        self.assertEqual(awareness["DaysSinceLogged"], 8)
        self.assertTrue(awareness["NeedsLogging"])

    def test_keeps_flagging_weight_after_the_eight_day_threshold(self) -> None:
        awareness = app._weight_logging_awareness(
            [{"LogDate": "2026-07-10", "WeightKg": 100.0}],
            "UTC",
            datetime(2026, 7, 20, 9, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(awareness["DaysSinceLogged"], 10)
        self.assertTrue(awareness["NeedsLogging"])

    def test_resting_heart_rate_alert_is_only_returned_when_claimed(self) -> None:
        items = [{"LogDate": "2026-07-20", "RestingHeartRate": 104}]
        with patch.object(app, "_claim_agent_alert", side_effect=[True, False]) as claim:
            first = app._resting_heart_rate_awareness(items, date(2026, 7, 20), "test-subject")
            second = app._resting_heart_rate_awareness(items, date(2026, 7, 20), "test-subject")

        self.assertTrue(first["Flagged"])
        self.assertEqual(first["RestingHeartRate"], 104)
        self.assertIn("trend cue", first["AgentNotice"])
        self.assertIsNone(second)
        claim.assert_any_call("test-subject", "resting-heart-rate:2026-07-20:104")

    def test_resting_heart_rate_alert_ignores_old_or_unflagged_readings(self) -> None:
        with patch.object(app, "_claim_agent_alert") as claim:
            old = app._resting_heart_rate_awareness(
                [{"LogDate": "2026-07-16", "RestingHeartRate": 110}], date(2026, 7, 20), "test-subject"
            )
            unflagged = app._resting_heart_rate_awareness(
                [{"LogDate": "2026-07-20", "RestingHeartRate": 72}], date(2026, 7, 20), "test-subject"
            )

        self.assertIsNone(old)
        self.assertIsNone(unflagged)
        claim.assert_not_called()

    def test_agent_alert_claim_is_persisted_per_alert(self) -> None:
        original_path = app.Config.state_db_path
        with tempfile.TemporaryDirectory() as directory:
            try:
                app.Config.state_db_path = os.path.join(directory, "health_mcp.sqlite3")
                app._init_db()
                self.assertTrue(app._claim_agent_alert("test-subject", "resting-heart-rate:2026-07-20:104"))
                self.assertFalse(app._claim_agent_alert("test-subject", "resting-heart-rate:2026-07-20:104"))
                self.assertTrue(app._claim_agent_alert("test-subject", "resting-heart-rate:2026-07-21:102"))
            finally:
                app.Config.state_db_path = original_path

    def test_weekly_review_is_due_sunday_evening_and_monday_morning(self) -> None:
        sunday = app._weekly_review_awareness("UTC", datetime(2026, 7, 19, 18, 0, tzinfo=timezone.utc))
        monday = app._weekly_review_awareness("UTC", datetime(2026, 7, 20, 9, 0, tzinfo=timezone.utc))
        monday_afternoon = app._weekly_review_awareness("UTC", datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc))

        self.assertTrue(sunday["Due"])
        self.assertTrue(monday["Due"])
        self.assertIsNone(monday_afternoon)

    def test_reminds_about_blank_dashboard_notes_after_dinner_and_next_day(self) -> None:
        current_day = {
            "DailyLog": {"Notes": ""},
            "Entries": [{"MealType": "Dinner"}],
        }
        yesterday = {
            "DailyLog": {"Notes": None},
            "Entries": [{"MealType": "Dinner"}],
        }

        awareness = app._dashboard_notes_awareness(current_day, yesterday, date(2026, 7, 20))

        self.assertTrue(awareness["NeedsLogging"])
        self.assertEqual([item["LogDate"] for item in awareness["Days"]], ["2026-07-20", "2026-07-19"])
        self.assertIn("2026-07-20", awareness["AgentNotice"])

    def test_does_not_remind_about_dashboard_notes_once_populated(self) -> None:
        summary = {
            "DailyLog": {"Notes": "Felt good after dinner."},
            "Entries": [{"MealType": "Dinner"}],
        }

        self.assertIsNone(app._dashboard_notes_awareness(summary, summary, date(2026, 7, 20)))

    def test_reminds_about_missing_dinner_reflection_scores_after_dinner(self) -> None:
        summary = {
            "DailyLog": {"HungerBeforeDinner": None, "OverallSatisfaction": 7},
            "Entries": [{"MealType": "Dinner"}],
        }

        awareness = app._dinner_reflection_awareness(summary, date(2026, 7, 20))

        self.assertEqual(awareness["MissingFields"], ["HungerBeforeDinner"])
        self.assertIn("hunger before dinner", awareness["AgentNotice"])

    def test_does_not_remind_about_dinner_reflection_when_scores_are_complete(self) -> None:
        summary = {
            "DailyLog": {"HungerBeforeDinner": 4, "OverallSatisfaction": 7},
            "Entries": [{"MealType": "Dinner"}],
        }

        self.assertIsNone(app._dinner_reflection_awareness(summary, date(2026, 7, 20)))

    def test_reminds_about_work_location_on_weekdays_until_recorded(self) -> None:
        awareness = app._daily_details_awareness({"DailyLog": None}, [], date(2026, 7, 20))

        self.assertTrue(awareness["OfficeMode"]["NeedsLogging"])
        self.assertIn("work location", awareness["AgentNotice"])

    def test_period_reminder_only_appears_in_estimated_cycle_window(self) -> None:
        history = [
            {"LogDate": "2026-05-01", "Period": True},
            {"LogDate": "2026-05-02", "Period": True},
            {"LogDate": "2026-05-29", "Period": True},
            {"LogDate": "2026-05-30", "Period": True},
            {"LogDate": "2026-06-26", "Period": True},
        ]

        near_due = app._period_cycle_awareness({"DailyLog": None}, history, date(2026, 7, 21))
        mid_cycle = app._period_cycle_awareness({"DailyLog": None}, history, date(2026, 7, 10))
        already_recorded = app._period_cycle_awareness({"DailyLog": {"Period": False}}, history, date(2026, 7, 21))

        self.assertEqual(near_due["PredictedStartDate"], "2026-07-24")
        self.assertEqual(near_due["EstimatedCycleDays"], 28)
        self.assertIsNone(mid_cycle)
        self.assertIsNone(already_recorded)


class HealthTaskToolsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.health_task = {
            "Id": 41,
            "Title": "Water at desk",
            "RelatedModule": "health",
            "ListName": "Personal",
        }
        self.other_task = {
            "Id": 42,
            "Title": "Take bins out",
            "RelatedModule": "chores",
            "ListName": "Home",
        }

    def test_list_health_tasks_filters_everday_tasks(self) -> None:
        with (
            patch.object(app, "_require_principal", return_value={"subject": "test-subject"}),
            patch.object(app, "_refresh_access_for_principal", return_value=("access-token", {})),
            patch.object(app, "_http_json", return_value={"Tasks": [self.health_task, self.other_task]}) as request,
        ):
            result = app._tool_list_health_tasks({"view": "open"}, {})

        self.assertEqual(result, {"Tasks": [self.health_task]})
        request.assert_called_once_with(
            "GET",
            "/api/tasks?view=open",
            headers={"Authorization": "Bearer access-token"},
        )

    def test_create_health_task_forces_health_module(self) -> None:
        with (
            patch.object(app, "_require_principal", return_value={"subject": "test-subject"}),
            patch.object(app, "_refresh_access_for_principal", return_value=("access-token", {})),
            patch.object(app, "_http_json", return_value=self.health_task) as request,
        ):
            result = app._tool_create_health_task(
                {"title": "Water at desk", "start_date": "2026-07-25", "start_time": "08:30"},
                {},
            )

        self.assertEqual(result, self.health_task)
        request.assert_called_once_with(
            "POST",
            "/api/tasks",
            payload={"Title": "Water at desk", "StartDate": "2026-07-25", "StartTime": "08:30", "RelatedModule": "health"},
            headers={"Authorization": "Bearer access-token"},
        )

    def test_update_health_task_checks_scope_before_updating(self) -> None:
        with (
            patch.object(app, "_require_principal", return_value={"subject": "test-subject"}),
            patch.object(app, "_refresh_access_for_principal", return_value=("access-token", {})),
            patch.object(app, "_http_json", side_effect=[{"Tasks": [self.health_task]}, self.health_task]) as request,
        ):
            result = app._tool_update_health_task({"task_id": 41, "start_time": "09:00"}, {})

        self.assertEqual(result, self.health_task)
        self.assertEqual(
            request.call_args_list,
            [
                call("GET", "/api/tasks?view=open", headers={"Authorization": "Bearer access-token"}),
                call(
                    "PUT",
                    "/api/tasks/41",
                    payload={"StartTime": "09:00", "RelatedModule": "health"},
                    headers={"Authorization": "Bearer access-token"},
                ),
            ],
        )

    def test_complete_and_snooze_reject_non_health_tasks(self) -> None:
        with (
            patch.object(app, "_require_principal", return_value={"subject": "test-subject"}),
            patch.object(app, "_refresh_access_for_principal", return_value=("access-token", {})),
            patch.object(app, "_http_json", return_value={"Tasks": [self.other_task]}) as request,
        ):
            with self.assertRaisesRegex(ValueError, "Health task not found"):
                app._tool_complete_health_task({"task_id": 42}, {})
            with self.assertRaisesRegex(ValueError, "Health task not found"):
                app._tool_snooze_health_task({"task_id": 42, "minutes": 15}, {})

        self.assertEqual(request.call_count, 2)
        self.assertTrue(all(call.args[0] == "GET" for call in request.call_args_list))

    def test_complete_and_snooze_health_tasks_call_everday(self) -> None:
        with (
            patch.object(app, "_require_principal", return_value={"subject": "test-subject"}),
            patch.object(app, "_refresh_access_for_principal", return_value=("access-token", {})),
            patch.object(
                app,
                "_http_json",
                side_effect=[{"Tasks": [self.health_task]}, {"Task": self.health_task}, {"Tasks": [self.health_task]}, self.health_task],
            ) as request,
        ):
            complete = app._tool_complete_health_task({"task_id": 41}, {})
            snoozed = app._tool_snooze_health_task({"task_id": 41, "minutes": 15}, {})

        self.assertEqual(complete, {"Task": self.health_task})
        self.assertEqual(snoozed, self.health_task)
        self.assertEqual(request.call_args_list[1].args[:2], ("POST", "/api/tasks/41/complete"))
        self.assertEqual(request.call_args_list[1].kwargs["payload"], {})
        self.assertEqual(request.call_args_list[3].args[:2], ("POST", "/api/tasks/41/snooze"))
        self.assertEqual(request.call_args_list[3].kwargs["payload"], {"Minutes": 15})

    def test_health_task_tool_annotations(self) -> None:
        self.assertIn("list_health_tasks", app.TOOLS)
        self.assertIn("update_health_task", app.IDEMPOTENT_WRITE_TOOLS)
        self.assertTrue(app._tool_annotations("list_health_tasks")["readOnlyHint"])
        self.assertTrue(app._tool_annotations("update_health_task")["idempotentHint"])
        self.assertFalse(app._tool_annotations("complete_health_task")["readOnlyHint"])


if __name__ == "__main__":
    unittest.main()
