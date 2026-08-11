from __future__ import annotations

from typing import Any


STRING = {"type": "string"}
BOOLEAN = {"type": "boolean"}
INTEGER = {"type": "integer"}
NUMBER = {"type": "number"}
OBJECT = {"type": "object"}
OBJECT_LIST = {"type": "array", "items": {"type": "object"}}
STRING_LIST = {"type": "array", "items": {"type": "string"}}


def _nullable(schema: dict[str, Any]) -> dict[str, Any]:
    return {"anyOf": [schema, {"type": "null"}]}


def _record_schema(
    required: dict[str, dict[str, Any]],
    optional: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {**required, **(optional or {})},
        "required": list(required),
        "additionalProperties": True,
    }


def _record_list(record_schema: dict[str, Any]) -> dict[str, Any]:
    return {"type": "array", "items": record_schema}


def _output_schema(
    required: dict[str, dict[str, Any]] | None = None,
    optional: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    required = required or {}
    properties = {
        **required,
        **(optional or {}),
        "TaskAwareness": OBJECT,
        "AgentNotice": STRING,
    }
    schema: dict[str, Any] = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": properties,
        # Everday may add fields without breaking the MCP contract. The named
        # fields below are the stable portion clients can safely depend on.
        "additionalProperties": True,
    }
    if required:
        schema["required"] = list(required)
    return schema


SUMMARY_FIELDS = {
    "Workouts": OBJECT_LIST,
    "Entries": OBJECT_LIST,
    "Totals": OBJECT,
    "Summary": OBJECT,
    "Targets": OBJECT,
}

MEAL_LOG_FIELDS = {
    "Created": BOOLEAN,
    "ParsedMeal": OBJECT,
    **SUMMARY_FIELDS,
}

TASK_FIELDS = {
    "Id": INTEGER,
    "Title": STRING,
}

HEADACHE_RECORD = _record_schema(
    {"HeadacheEventId": STRING, "LogDate": STRING, "EventType": STRING},
    {
        "OnsetAt": _nullable(STRING),
        "Severity": _nullable(INTEGER),
        "Location": _nullable(STRING),
        "ContextNotes": _nullable(STRING),
    },
)

MEDICATION_REQUIRED = {
    "MedicationDoseId": STRING,
    "LogDate": STRING,
    "MedicationName": STRING,
}
MEDICATION_OPTIONAL = {
    "HeadacheEventId": _nullable(STRING),
    "TakenAt": _nullable(STRING),
    "Dose": _nullable(STRING),
    "Notes": _nullable(STRING),
}
MEDICATION_RECORD = _record_schema(MEDICATION_REQUIRED, MEDICATION_OPTIONAL)

TASK_RECORD = _record_schema(TASK_FIELDS)


OUTPUT_SCHEMAS: dict[str, dict[str, Any]] = {
    "start_account_link": _output_schema(
        {
            "status": STRING,
            "external_subject": STRING,
            "link_url": STRING,
            "expires_at": STRING,
            "instructions": STRING,
        },
        {"external_email": _nullable(STRING)},
    ),
    "connect_account": _output_schema(
        {
            "status": STRING,
            "external_subject": STRING,
            "everday_user_id": INTEGER,
            "everday_username": STRING,
            "profile": OBJECT,
        },
        {"external_email": _nullable(STRING)},
    ),
    "disconnect_account": _output_schema(
        {
            "status": STRING,
            "external_subject": STRING,
            "everday_user_id": INTEGER,
            "everday_username": STRING,
        }
    ),
    "connection_status": _output_schema(
        {"linked": BOOLEAN, "external_subject": STRING, "Server": OBJECT},
        {
            "external_email": _nullable(STRING),
            "everday_user_id": INTEGER,
            "everday_username": STRING,
            "created_at": STRING,
            "updated_at": STRING,
            "pending_link": OBJECT,
        },
    ),
    "log_meal_text": _output_schema(
        MEAL_LOG_FIELDS,
        {"Reason": _nullable(STRING), "MealEntryId": _nullable(STRING), "DailyLog": _nullable(OBJECT)},
    ),
    "log_meal_image": _output_schema(
        MEAL_LOG_FIELDS,
        {"Reason": _nullable(STRING), "MealEntryId": _nullable(STRING), "DailyLog": _nullable(OBJECT)},
    ),
    "log_meal_manual": _output_schema(
        MEAL_LOG_FIELDS,
        {"Reason": _nullable(STRING), "MealEntryId": _nullable(STRING), "DailyLog": _nullable(OBJECT)},
    ),
    "update_meal": _output_schema(
        {
            "Updated": BOOLEAN,
            "ParsedMeal": OBJECT,
            "MealEntryId": STRING,
            **SUMMARY_FIELDS,
            "PreviousLogDate": STRING,
            "TargetLogDate": STRING,
        },
        {"Reason": _nullable(STRING), "DailyLog": _nullable(OBJECT)},
    ),
    "delete_meal": _output_schema(
        {"Deleted": BOOLEAN, "MealEntryId": STRING, "LogDate": STRING, **SUMMARY_FIELDS},
        {"DailyLog": _nullable(OBJECT)},
    ),
    "log_weight": _output_schema(
        {"DailyLog": OBJECT, "RecentWeights": OBJECT_LIST, **SUMMARY_FIELDS}
    ),
    "log_headache": _output_schema(
        {"Headache": HEADACHE_RECORD, "MedicationDose": _nullable(MEDICATION_RECORD)}
    ),
    "log_medication_dose": _output_schema(
        MEDICATION_REQUIRED,
        MEDICATION_OPTIONAL,
    ),
    "get_headaches": _output_schema({"Items": _record_list(HEADACHE_RECORD)}),
    "get_medication_doses": _output_schema({"Items": _record_list(MEDICATION_RECORD)}),
    "update_daily_log": _output_schema(SUMMARY_FIELDS, {"DailyLog": _nullable(OBJECT)}),
    "log_workout": _output_schema(
        {"Workout": OBJECT, **SUMMARY_FIELDS}, {"DailyLog": _nullable(OBJECT)}
    ),
    "update_workout": _output_schema(
        {
            "Updated": BOOLEAN,
            "Workout": OBJECT,
            "PreviousLogDate": STRING,
            "TargetLogDate": STRING,
            **SUMMARY_FIELDS,
        },
        {"DailyLog": _nullable(OBJECT)},
    ),
    "delete_workout": _output_schema(
        {"Deleted": BOOLEAN, "WorkoutId": STRING, "LogDate": STRING, "DeletedWorkout": OBJECT}
    ),
    "get_today_summary": _output_schema(SUMMARY_FIELDS, {"DailyLog": _nullable(OBJECT)}),
    "get_goals": _output_schema({"Targets": OBJECT, "Goal": _nullable(OBJECT)}),
    "list_health_tasks": _output_schema({"Tasks": _record_list(TASK_RECORD)}),
    "create_health_task": _output_schema(TASK_FIELDS),
    "update_health_task": _output_schema(TASK_FIELDS),
    "complete_health_task": _output_schema(
        {"Task": TASK_RECORD, "NextTask": _nullable(TASK_RECORD)}
    ),
    "snooze_health_task": _output_schema(TASK_FIELDS),
    "reopen_health_task": _output_schema(TASK_FIELDS),
    "delete_health_task": _output_schema(),
    "update_targets": _output_schema({"Targets": OBJECT}),
    "set_goal": _output_schema({"Goal": OBJECT, "Targets": OBJECT}),
    "preview_goal_recommendation": _output_schema(
        {
            "DailyCalorieTarget": INTEGER,
            "ProteinTargetMin": NUMBER,
            "ProteinTargetMax": NUMBER,
            "Explanation": STRING,
        },
        {"Goal": _nullable(OBJECT), "ModelUsed": _nullable(STRING)},
    ),
    "get_connection_context": _output_schema(
        {
            "ReminderTimeZone": STRING,
            "TodayLayout": STRING_LIST,
            "MealSlots": OBJECT_LIST,
            "HistoryTypes": OBJECT_LIST,
            "WorkoutTypes": OBJECT_LIST,
            "DailyLogFields": OBJECT_LIST,
            "external_subject": STRING,
            "everday_user_id": INTEGER,
            "everday_username": STRING,
            "server_date": STRING,
            "Server": OBJECT,
        },
        {"external_email": _nullable(STRING)},
    ),
    "get_meal_type_options": _output_schema({"MealSlots": OBJECT_LIST}),
    "get_daily_log_fields": _output_schema(
        {"DailyLogFields": OBJECT_LIST},
        {"ReminderTimeZone": _nullable(STRING), "server_date": _nullable(STRING)},
    ),
    "get_weight_trend": _output_schema(
        {"Days": INTEGER, "StartDate": STRING, "EndDate": STRING, "Items": OBJECT_LIST},
        {"DeltaKg": _nullable(NUMBER)},
    ),
    "get_step_summary": _output_schema(
        {
            "StartDate": STRING,
            "EndDate": STRING,
            "Items": OBJECT_LIST,
            "TotalSteps": INTEGER,
            "AverageSteps": NUMBER,
            "TotalCaloriesBurnedFromSteps": INTEGER,
            "AverageCaloriesBurnedFromSteps": NUMBER,
            "StepTarget": INTEGER,
            "StepKcalFactor": NUMBER,
        }
    ),
    "get_targets_history": _output_schema({"CurrentTargets": OBJECT, "Items": OBJECT_LIST}),
    "get_meal": _output_schema({"Item": OBJECT}),
    "get_today_meals": _output_schema(
        {"Items": OBJECT_LIST},
        {
            "LogDate": _nullable(STRING),
            "DailyLog": _nullable(OBJECT),
            "Workouts": OBJECT_LIST,
            "Totals": _nullable(OBJECT),
            "Summary": _nullable(OBJECT),
            "Targets": _nullable(OBJECT),
        },
    ),
    "search_meals": _output_schema(
        {"Items": OBJECT_LIST},
        {"LogDate": _nullable(STRING), "Query": _nullable(STRING)},
    ),
    "save_food_from_meal": _output_schema(
        {"Saved": BOOLEAN, "MealEntryId": STRING, "Food": OBJECT, "Mode": STRING}
    ),
    "get_meal_slots": _output_schema({"MealSlots": OBJECT_LIST}),
    "get_history_types": _output_schema({"HistoryTypes": OBJECT_LIST}),
    "get_history_type_options": _output_schema({"HistoryTypes": OBJECT_LIST}),
    "get_workout_type_options": _output_schema({"WorkoutTypes": OBJECT_LIST}),
    "get_history": _output_schema({"HistoryType": STRING, "Items": OBJECT_LIST}),
    "search_saved_foods": _output_schema({"Items": OBJECT_LIST}),
    "get_saved_foods": _output_schema({"Items": OBJECT_LIST}),
    "upsert_recipe_review": _output_schema(
        {"RecipeReviewId": STRING, "RecipeName": STRING, "LogDate": STRING}
    ),
    "get_recipe_reviews": _output_schema({"Items": OBJECT_LIST}),
    "get_recipe_stats": _output_schema({"Items": OBJECT_LIST}),
    "upsert_product_review": _output_schema(
        {"ProductReviewId": STRING, "ProductName": STRING}
    ),
    "get_product_reviews": _output_schema({"Items": OBJECT_LIST}),
    "upsert_experiment": _output_schema(
        {"ExperimentId": STRING, "StartDate": STRING, "VariableChanged": STRING, "Status": STRING}
    ),
    "get_experiments": _output_schema({"Items": OBJECT_LIST}),
    "upsert_measurement": _output_schema(
        {"BodyMeasurementId": STRING, "LogDate": STRING}
    ),
    "get_measurements": _output_schema({"Items": OBJECT_LIST}),
    "upsert_weekly_review_note": _output_schema(
        {"WeeklyReviewNoteId": STRING, "WeekStart": STRING}
    ),
    "get_weekly_review_note": _output_schema({"Item": _nullable(OBJECT)}),
    "get_weekly_review": _output_schema({"Item": OBJECT}),
    "get_insight_type_options": _output_schema(
        {"InsightTypes": OBJECT_LIST, "PeriodTypes": OBJECT_LIST, "StatusValues": STRING_LIST}
    ),
    "upsert_insight": _output_schema(
        {
            "InsightId": STRING,
            "InsightType": STRING,
            "PeriodType": STRING,
            "PeriodStart": STRING,
            "Title": STRING,
            "Status": STRING,
            "Source": STRING,
            "SchemaVersion": INTEGER,
            "Tags": STRING_LIST,
        }
    ),
    "get_insights": _output_schema({"Items": OBJECT_LIST}),
}
