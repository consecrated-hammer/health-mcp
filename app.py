import json
import os
import secrets
import sqlite3
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from cryptography.fernet import Fernet, InvalidToken


def _require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _build_version() -> str:
    version_file = os.environ.get("HEALTH_MCP_VERSION_FILE", "/app/version.txt").strip() or "/app/version.txt"
    try:
        with open(version_file, "r", encoding="utf-8") as handle:
            value = handle.read().strip()
            if value:
                return value
    except FileNotFoundError:
        pass
    except OSError:
        pass
    return os.environ.get("HEALTH_MCP_VERSION", "dev").strip() or "dev"


class Config:
    host = os.environ.get("HEALTH_MCP_HOST", "0.0.0.0").strip() or "0.0.0.0"
    port = int(os.environ.get("HEALTH_MCP_PORT", "8766"))
    version = _build_version()
    provider = os.environ.get("HEALTH_MCP_PROVIDER", "authelia").strip() or "authelia"
    public_base_url = os.environ.get("HEALTH_MCP_PUBLIC_BASE_URL", "").strip().rstrip("/")
    everday_base_url = _require_env("HEALTH_MCP_EVERDAY_BASE_URL").rstrip("/")
    state_db_path = os.environ.get("HEALTH_MCP_STATE_DB_PATH", "/data/health_mcp.sqlite3").strip() or "/data/health_mcp.sqlite3"
    encryption_key = _require_env("HEALTH_MCP_ENCRYPTION_KEY")
    timeout_seconds = max(float(os.environ.get("HEALTH_MCP_TIMEOUT_SECONDS", "30")), 1.0)
    max_request_bytes = max(int(os.environ.get("HEALTH_MCP_MAX_REQUEST_BYTES", "10485760")), 1)
    link_session_ttl_minutes = max(int(os.environ.get("HEALTH_MCP_LINK_SESSION_TTL_MINUTES", "30")), 1)


MEAL_SLOT_OPTIONS = [
    {"value": "Breakfast", "label": "Breakfast", "aliases": ["breakfast"], "order": 1},
    {"value": "Snack1", "label": "Morning snack", "aliases": ["morning snack", "snack1", "snack 1"], "order": 2},
    {"value": "Lunch", "label": "Lunch", "aliases": ["lunch"], "order": 3},
    {"value": "Snack2", "label": "Afternoon snack", "aliases": ["afternoon snack", "bridge", "snack2", "snack 2"], "order": 4},
    {"value": "Dinner", "label": "Dinner", "aliases": ["dinner"], "order": 5},
    {"value": "Snack3", "label": "Evening snack", "aliases": ["evening snack", "dessert", "night snack", "snack3", "snack 3"], "order": 6},
]

MEAL_SLOT_ENUM = [item["value"] for item in MEAL_SLOT_OPTIONS]

HISTORY_TYPE_OPTIONS = [
    {"value": "weight", "description": "Per-day weight history."},
    {"value": "steps", "description": "Per-day step history and step calories."},
    {"value": "workouts", "description": "Workout ledger entries."},
    {"value": "days", "description": "Per-day calorie and burn summaries with workbook-style daily metadata."},
    {"value": "meals", "description": "Meal entry history with ids, slot labels, and nutrition metadata."},
]

HISTORY_TYPE_ENUM = [item["value"] for item in HISTORY_TYPE_OPTIONS]

WORKOUT_TYPE_OPTIONS = [
    {"value": "walk", "label": "Walk", "aliases": ["walk", "walking", "steps walk"]},
    {"value": "run", "label": "Run", "aliases": ["run", "running", "jog", "jogging"]},
    {"value": "cycle", "label": "Cycle", "aliases": ["cycle", "cycling", "bike", "biking"]},
    {"value": "strength", "label": "Strength", "aliases": ["strength", "weights", "lifting", "gym"]},
    {"value": "hiit", "label": "HIIT", "aliases": ["hiit", "intervals", "interval training"]},
    {"value": "yoga", "label": "Yoga", "aliases": ["yoga"]},
    {"value": "pilates", "label": "Pilates", "aliases": ["pilates", "reformer"]},
    {"value": "swim", "label": "Swim", "aliases": ["swim", "swimming"]},
    {"value": "sport", "label": "Sport", "aliases": ["sport", "sports"]},
    {"value": "other", "label": "Other", "aliases": ["other"]},
]

WORKOUT_TYPE_ENUM = [item["value"] for item in WORKOUT_TYPE_OPTIONS]

INSIGHT_TYPE_OPTIONS = [
    {"value": "context_comparison", "label": "Context comparison", "description": "Comparison between contexts such as office vs WFH."},
    {"value": "relationship_test", "label": "Relationship test", "description": "Observed relationship or correlation test between two metrics."},
    {"value": "meal_pattern", "label": "Meal pattern", "description": "Observed meal, recipe, or satiety pattern."},
    {"value": "weekly_pattern", "label": "Weekly pattern", "description": "Insight spanning a weekly period."},
    {"value": "custom", "label": "Custom", "description": "A user-defined or workbook-specific insight type."},
]

INSIGHT_PERIOD_OPTIONS = [
    {"value": "day", "label": "Day"},
    {"value": "week", "label": "Week"},
    {"value": "month", "label": "Month"},
    {"value": "custom", "label": "Custom"},
]

INSIGHT_PERIOD_ENUM = [item["value"] for item in INSIGHT_PERIOD_OPTIONS]
INSIGHT_STATUS_ENUM = ["active", "superseded", "archived"]

READ_ONLY_TOOLS = {
    "connection_status",
    "get_connection_context",
    "get_daily_log_fields",
    "get_experiments",
    "get_goals",
    "get_history",
    "get_history_type_options",
    "get_history_types",
    "get_insight_type_options",
    "get_insights",
    "get_meal",
    "get_meal_slots",
    "get_meal_type_options",
    "get_measurements",
    "get_product_reviews",
    "get_recipe_reviews",
    "get_recipe_stats",
    "get_saved_foods",
    "get_step_summary",
    "get_targets_history",
    "get_today_meals",
    "get_today_summary",
    "get_weight_trend",
    "get_weekly_review",
    "get_weekly_review_note",
    "get_workout_type_options",
    "search_meals",
    "search_saved_foods",
}

DESTRUCTIVE_TOOLS = {
    "delete_meal",
    "delete_workout",
    "disconnect_account",
}

IDEMPOTENT_WRITE_TOOLS = {
    "update_daily_log",
    "update_meal",
    "update_targets",
    "update_workout",
    "upsert_experiment",
    "upsert_insight",
    "upsert_measurement",
    "upsert_product_review",
    "upsert_recipe_review",
    "upsert_weekly_review_note",
}

TASK_AWARENESS_UPCOMING_MINUTES = 120
TASK_AWARENESS_WEIGHT_DAYS = 8
RESTING_HEART_RATE_ALERT_THRESHOLD = 100
RESTING_HEART_RATE_ALERT_MAX_AGE_DAYS = 3
WEEKLY_REVIEW_SUNDAY_START_HOUR = 18
WEEKLY_REVIEW_MONDAY_END_HOUR = 12
PERIOD_CYCLE_MIN_DAYS = 20
PERIOD_CYCLE_MAX_DAYS = 45
PERIOD_REMINDER_DAYS_BEFORE = 3
PERIOD_REMINDER_DAYS_AFTER = 4


_db_lock = threading.Lock()
_cipher = Fernet(Config.encryption_key.encode("utf-8"))


def _utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _today_iso() -> str:
    return date.today().isoformat()


def _parse_iso_datetime(value: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _init_db() -> None:
    parent = os.path.dirname(Config.state_db_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with _db_connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS linked_accounts (
                external_subject TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                external_email TEXT,
                external_name TEXT,
                everday_user_id INTEGER NOT NULL,
                everday_username TEXT NOT NULL,
                refresh_token_ciphertext TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                revoked_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS account_link_sessions (
                session_token TEXT PRIMARY KEY,
                external_subject TEXT NOT NULL,
                external_email TEXT,
                external_name TEXT,
                status TEXT NOT NULL,
                everday_user_id INTEGER,
                everday_username TEXT,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                completed_at TEXT,
                last_error TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_account_link_sessions_subject_status
            ON account_link_sessions (external_subject, status)
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS delivered_agent_alerts (
                external_subject TEXT NOT NULL,
                alert_key TEXT NOT NULL,
                delivered_at TEXT NOT NULL,
                PRIMARY KEY (external_subject, alert_key)
            )
            """
        )
        conn.commit()


def _db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(Config.state_db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _claim_agent_alert(external_subject: str, alert_key: str) -> bool:
    with _db_lock, _db_connect() as conn:
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO delivered_agent_alerts (external_subject, alert_key, delivered_at)
            VALUES (?, ?, ?)
            """,
            (external_subject, alert_key, _utc_now().isoformat()),
        )
        conn.commit()
    return cursor.rowcount == 1


def _encrypt(value: str) -> str:
    return _cipher.encrypt(value.encode("utf-8")).decode("utf-8")


def _decrypt(value: str) -> str:
    try:
        return _cipher.decrypt(value.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise RuntimeError("Stored account credentials cannot be decrypted.") from exc


def _json_response(result: Any, *, tool_error: bool = False) -> dict[str, Any]:
    text = json.dumps(result, ensure_ascii=True, indent=2, default=str)
    payload = {
        "content": [{"type": "text", "text": text}],
        "structuredContent": result,
    }
    if tool_error:
        payload["isError"] = True
    return payload


def _load_account(external_subject: str) -> sqlite3.Row | None:
    with _db_lock, _db_connect() as conn:
        return conn.execute(
            """
            SELECT *
            FROM linked_accounts
            WHERE external_subject = ? AND revoked_at IS NULL
            """,
            (external_subject,),
        ).fetchone()


def _save_account(
    external_subject: str,
    external_email: str | None,
    external_name: str | None,
    everday_user_id: int,
    everday_username: str,
    refresh_token: str,
) -> None:
    now = _utc_now().isoformat()
    ciphertext = _encrypt(refresh_token)
    with _db_lock, _db_connect() as conn:
        conn.execute(
            """
            INSERT INTO linked_accounts (
                external_subject,
                provider,
                external_email,
                external_name,
                everday_user_id,
                everday_username,
                refresh_token_ciphertext,
                created_at,
                updated_at,
                revoked_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
            ON CONFLICT(external_subject) DO UPDATE SET
                provider = excluded.provider,
                external_email = excluded.external_email,
                external_name = excluded.external_name,
                everday_user_id = excluded.everday_user_id,
                everday_username = excluded.everday_username,
                refresh_token_ciphertext = excluded.refresh_token_ciphertext,
                updated_at = excluded.updated_at,
                revoked_at = NULL
            """,
            (
                external_subject,
                Config.provider,
                external_email,
                external_name,
                everday_user_id,
                everday_username,
                ciphertext,
                now,
                now,
            ),
        )
        conn.commit()


def _revoke_account(external_subject: str) -> None:
    with _db_lock, _db_connect() as conn:
        conn.execute(
            """
            UPDATE linked_accounts
            SET revoked_at = ?, updated_at = ?
            WHERE external_subject = ? AND revoked_at IS NULL
            """,
            (_utc_now().isoformat(), _utc_now().isoformat(), external_subject),
        )
        conn.commit()


def _replace_refresh_token(external_subject: str, refresh_token: str) -> None:
    with _db_lock, _db_connect() as conn:
        conn.execute(
            """
            UPDATE linked_accounts
            SET refresh_token_ciphertext = ?, updated_at = ?
            WHERE external_subject = ? AND revoked_at IS NULL
            """,
            (_encrypt(refresh_token), _utc_now().isoformat(), external_subject),
        )
        conn.commit()


def _expire_pending_link_sessions(external_subject: str) -> None:
    now = _utc_now().isoformat()
    with _db_lock, _db_connect() as conn:
        conn.execute(
            """
            UPDATE account_link_sessions
            SET status = 'expired',
                last_error = COALESCE(last_error, 'Superseded by a newer link session.')
            WHERE external_subject = ?
              AND status = 'pending'
            """,
            (external_subject,),
        )
        conn.commit()


def _create_link_session(principal: dict[str, str | None]) -> dict[str, Any]:
    external_subject = principal["subject"] or ""
    _expire_pending_link_sessions(external_subject)
    session_token = secrets.token_urlsafe(24)
    created_at = _utc_now()
    expires_at = created_at + timedelta(minutes=Config.link_session_ttl_minutes)
    with _db_lock, _db_connect() as conn:
        conn.execute(
            """
            INSERT INTO account_link_sessions (
                session_token,
                external_subject,
                external_email,
                external_name,
                status,
                everday_user_id,
                everday_username,
                created_at,
                expires_at,
                completed_at,
                last_error
            ) VALUES (?, ?, ?, ?, 'pending', NULL, NULL, ?, ?, NULL, NULL)
            """,
            (
                session_token,
                external_subject,
                principal.get("email"),
                principal.get("name"),
                created_at.isoformat(),
                expires_at.isoformat(),
            ),
        )
        conn.commit()
    link_url = f"{Config.public_base_url}/link/{session_token}" if Config.public_base_url else f"/link/{session_token}"
    return {
        "session_token": session_token,
        "external_subject": external_subject,
        "external_email": principal.get("email"),
        "created_at": created_at.isoformat(),
        "expires_at": expires_at.isoformat(),
        "link_url": link_url,
    }


def _load_link_session(session_token: str) -> sqlite3.Row | None:
    with _db_lock, _db_connect() as conn:
        return conn.execute(
            """
            SELECT *
            FROM account_link_sessions
            WHERE session_token = ?
            """,
            (session_token,),
        ).fetchone()


def _load_pending_link_session(external_subject: str) -> sqlite3.Row | None:
    with _db_lock, _db_connect() as conn:
        return conn.execute(
            """
            SELECT *
            FROM account_link_sessions
            WHERE external_subject = ?
              AND status = 'pending'
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (external_subject,),
        ).fetchone()


def _mark_link_session(
    session_token: str,
    *,
    status: str,
    everday_user_id: int | None = None,
    everday_username: str | None = None,
    completed_at: str | None = None,
    last_error: str | None = None,
) -> None:
    with _db_lock, _db_connect() as conn:
        conn.execute(
            """
            UPDATE account_link_sessions
            SET status = ?,
                everday_user_id = ?,
                everday_username = ?,
                completed_at = ?,
                last_error = ?
            WHERE session_token = ?
            """,
            (status, everday_user_id, everday_username, completed_at, last_error, session_token),
        )
        conn.commit()


def _active_link_session_or_error(session_token: str) -> sqlite3.Row:
    session = _load_link_session(session_token)
    if session is None:
        raise ValueError("This account-link session does not exist.")
    if session["status"] == "completed":
        return session
    if session["status"] != "pending":
        raise ValueError("This account-link session is no longer active.")
    if _parse_iso_datetime(session["expires_at"]) <= _utc_now():
        _mark_link_session(
            session_token,
            status="expired",
            everday_user_id=session["everday_user_id"],
            everday_username=session["everday_username"],
            completed_at=session["completed_at"],
            last_error="The account-link session expired before completion.",
        )
        raise ValueError("This account-link session has expired.")
    return session


def _http_json(
    method: str,
    path: str,
    *,
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    url = f"{Config.everday_base_url}{path}"
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=body, method=method)
    request.add_header("Accept", "application/json")
    if payload is not None:
        request.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        request.add_header(key, value)

    try:
        with urllib.request.urlopen(request, timeout=Config.timeout_seconds) as response:
            raw = response.read()
            return json.loads(raw.decode("utf-8")) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            data = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            data = {"detail": raw or exc.reason}
        detail = data.get("detail") or data.get("error") or exc.reason
        raise ValueError(f"Everday API error ({exc.code}): {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Failed to reach Everday: {exc.reason}") from exc


def _everday_login(username: str, password: str) -> dict[str, Any]:
    return _http_json(
        "POST",
        "/api/auth/login",
        payload={"Username": username, "Password": password},
    )


def _everday_refresh(refresh_token: str) -> dict[str, Any]:
    return _http_json(
        "POST",
        "/api/auth/refresh",
        payload={"RefreshToken": refresh_token},
    )


def _authorized_headers(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


def _task_timezone(value: Any, fallback: str) -> ZoneInfo:
    for candidate in (value, fallback, "UTC"):
        try:
            return ZoneInfo(str(candidate))
        except (TypeError, ValueError, ZoneInfoNotFoundError):
            continue
    return ZoneInfo("UTC")


def _task_applies_on_date(task: dict[str, Any], local_date: date) -> bool:
    try:
        start_date = date.fromisoformat(str(task.get("StartDate") or ""))
    except ValueError:
        return False
    if local_date < start_date:
        return False
    repeat_until = task.get("RepeatUntilDate")
    if repeat_until:
        try:
            if local_date > date.fromisoformat(str(repeat_until)):
                return False
        except ValueError:
            return False
    if str(task.get("RepeatType") or "none").lower() != "weekly":
        return local_date == start_date or local_date > start_date
    weekdays = [int(day) for day in (task.get("RepeatWeekdays") or []) if str(day).isdigit()]
    if not weekdays or local_date.weekday() not in weekdays:
        return False
    interval = max(1, int(task.get("RepeatInterval") or 1))
    week_offset = (local_date - start_date).days // 7
    return week_offset % interval == 0


def _task_occurrence_datetime(task: dict[str, Any], local_date: date, fallback_timezone: str) -> datetime | None:
    start_time = str(task.get("StartTime") or "").strip()
    if not start_time:
        return None
    try:
        parsed_time = datetime.strptime(start_time, "%H:%M").time()
    except ValueError:
        return None
    return datetime.combine(local_date, parsed_time, tzinfo=_task_timezone(task.get("TimeZone"), fallback_timezone))


def _health_task_items(tasks: list[dict[str, Any]], user_id: int, reminder_timezone: str, now_utc: datetime) -> dict[str, Any]:
    overdue: list[dict[str, Any]] = []
    upcoming: list[dict[str, Any]] = []
    for task in tasks:
        if task.get("IsCompleted") or int(task.get("OwnerUserId") or 0) != user_id:
            continue
        related_module = str(task.get("RelatedModule") or "").lower()
        list_name = str(task.get("ListName") or "").lower()
        if related_module != "health" and list_name != "health":
            continue
        task_timezone = _task_timezone(task.get("TimeZone"), reminder_timezone)
        local_now = now_utc.astimezone(task_timezone)
        local_date = local_now.date()
        if not _task_applies_on_date(task, local_date):
            continue
        due_at = _task_occurrence_datetime(task, local_date, reminder_timezone)
        if due_at is None:
            continue
        item = {
            "Id": task.get("Id"),
            "Title": task.get("Title"),
            "DueAt": due_at.isoformat(),
            "DueTime": due_at.strftime("%H:%M"),
            "TimeZone": str(task_timezone),
        }
        if due_at <= local_now:
            overdue.append(item)
        elif due_at <= local_now + timedelta(minutes=TASK_AWARENESS_UPCOMING_MINUTES):
            upcoming.append(item)
    overdue.sort(key=lambda item: str(item["DueAt"]))
    upcoming.sort(key=lambda item: str(item["DueAt"]))
    notices = []
    if overdue:
        notices.append("Overdue health tasks: " + "; ".join(f"{item['Title']} (due {item['DueTime']})" for item in overdue))
    if upcoming:
        notices.append(
            f"Upcoming health tasks in the next {TASK_AWARENESS_UPCOMING_MINUTES // 60} hours: "
            + "; ".join(f"{item['Title']} (due {item['DueTime']})" for item in upcoming)
        )
    return {"Overdue": overdue, "Upcoming": upcoming, "AgentNotice": ". ".join(notices) or None}


def _weight_logging_awareness(items: list[dict[str, Any]], reminder_timezone: str, now_utc: datetime) -> dict[str, Any]:
    logged_dates = []
    for item in items:
        try:
            logged_dates.append(date.fromisoformat(str(item.get("LogDate") or "")))
        except ValueError:
            continue
    last_logged_date = max(logged_dates) if logged_dates else None
    local_today = now_utc.astimezone(_task_timezone(None, reminder_timezone)).date()
    days_since_logged = max(0, (local_today - last_logged_date).days) if last_logged_date else None
    return {
        "LastLoggedDate": last_logged_date.isoformat() if last_logged_date else None,
        "DaysSinceLogged": days_since_logged,
        "NeedsLogging": last_logged_date is None or days_since_logged >= TASK_AWARENESS_WEIGHT_DAYS,
    }


def _weekly_review_awareness(reminder_timezone: str, now_utc: datetime) -> dict[str, Any] | None:
    local_now = now_utc.astimezone(_task_timezone(None, reminder_timezone))
    is_due_window = (
        (local_now.weekday() == 6 and local_now.hour >= WEEKLY_REVIEW_SUNDAY_START_HOUR)
        or (local_now.weekday() == 0 and local_now.hour < WEEKLY_REVIEW_MONDAY_END_HOUR)
    )
    if not is_due_window:
        return None
    return {
        "Due": True,
        "WeekStart": (local_now.date() - timedelta(days=local_now.weekday())).isoformat(),
        "Window": "Sunday evening to Monday morning",
        "AgentNotice": "Weekly review is due. Work with the user to summarise and reflect on the previous week.",
    }


def _dashboard_notes_awareness(
    today_summary: dict[str, Any],
    previous_summary: dict[str, Any],
    local_today: date,
) -> dict[str, Any] | None:
    reminders: list[dict[str, str]] = []
    for log_date, summary, reason in (
        (local_today, today_summary, "Dinner has been logged"),
        (local_today - timedelta(days=1), previous_summary, "Yesterday's dinner was logged"),
    ):
        daily_log = summary.get("DailyLog")
        entries = summary.get("Entries")
        if not isinstance(daily_log, dict) or not isinstance(entries, list):
            continue
        has_dinner = any(
            isinstance(entry, dict) and str(entry.get("MealType") or "").strip().lower() == "dinner"
            for entry in entries
        )
        has_notes = bool(str(daily_log.get("Notes") or "").strip())
        if has_dinner and not has_notes:
            reminders.append({"LogDate": log_date.isoformat(), "Reason": reason})
    if not reminders:
        return None
    dates = "; ".join(item["LogDate"] for item in reminders)
    return {
        "NeedsLogging": True,
        "Days": reminders,
        "AgentNotice": f"Dashboard notes are still blank for {dates}. Work with the user to capture a concise day summary.",
    }


def _dinner_reflection_awareness(today_summary: dict[str, Any], local_today: date) -> dict[str, Any] | None:
    daily_log = today_summary.get("DailyLog")
    entries = today_summary.get("Entries")
    if not isinstance(daily_log, dict) or not isinstance(entries, list):
        return None
    has_dinner = any(
        isinstance(entry, dict) and str(entry.get("MealType") or "").strip().lower() == "dinner"
        for entry in entries
    )
    if not has_dinner:
        return None
    missing = []
    if daily_log.get("HungerBeforeDinner") is None:
        missing.append("HungerBeforeDinner")
    if daily_log.get("OverallSatisfaction") is None:
        missing.append("OverallSatisfaction")
    if not missing:
        return None
    labels = {
        "HungerBeforeDinner": "hunger before dinner",
        "OverallSatisfaction": "overall satisfaction",
    }
    return {
        "NeedsLogging": True,
        "LogDate": local_today.isoformat(),
        "MissingFields": missing,
        "AgentNotice": "Dinner has been logged but "
        + " and ".join(labels[field] for field in missing)
        + " still need a score. Work with the user to capture the day reflection.",
    }


def _period_status_is_recorded(daily_log: dict[str, Any] | None) -> bool:
    if not isinstance(daily_log, dict):
        return False
    if isinstance(daily_log.get("Period"), bool):
        return True
    return bool(str(daily_log.get("PeriodLabel") or "").strip())


def _period_day(item: dict[str, Any]) -> bool:
    if item.get("Period") is True:
        return True
    label = str(item.get("PeriodLabel") or "").strip().lower()
    return bool(label and label not in {"no", "none", "false", "not on period"})


def _median_int(values: list[int]) -> int:
    ordered = sorted(values)
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[midpoint]
    return round((ordered[midpoint - 1] + ordered[midpoint]) / 2)


def _period_cycle_awareness(
    today_summary: dict[str, Any],
    historical_days: list[dict[str, Any]],
    local_today: date,
) -> dict[str, Any] | None:
    if _period_status_is_recorded(today_summary.get("DailyLog")):
        return None
    period_dates: list[date] = []
    for item in historical_days:
        if not isinstance(item, dict) or not _period_day(item):
            continue
        try:
            period_dates.append(date.fromisoformat(str(item.get("LogDate") or "")))
        except ValueError:
            continue
    period_dates = sorted(set(period_dates))
    period_starts = [
        period_date
        for index, period_date in enumerate(period_dates)
        if index == 0 or period_date - period_dates[index - 1] > timedelta(days=1)
    ]
    intervals = [
        (current - previous).days
        for previous, current in zip(period_starts, period_starts[1:])
        if PERIOD_CYCLE_MIN_DAYS <= (current - previous).days <= PERIOD_CYCLE_MAX_DAYS
    ]
    if not intervals:
        return None
    cycle_days = _median_int(intervals[-6:])
    predicted_start = period_starts[-1] + timedelta(days=cycle_days)
    window_start = predicted_start - timedelta(days=PERIOD_REMINDER_DAYS_BEFORE)
    window_end = predicted_start + timedelta(days=PERIOD_REMINDER_DAYS_AFTER)
    if not window_start <= local_today <= window_end:
        return None
    return {
        "NeedsLogging": True,
        "PredictedStartDate": predicted_start.isoformat(),
        "WindowStartDate": window_start.isoformat(),
        "WindowEndDate": window_end.isoformat(),
        "EstimatedCycleDays": cycle_days,
        "AgentNotice": "Period status has not been logged and is due around now. Work with the user to record it if appropriate.",
    }


def _daily_details_awareness(
    today_summary: dict[str, Any],
    historical_days: list[dict[str, Any]],
    local_today: date,
) -> dict[str, Any] | None:
    daily_log = today_summary.get("DailyLog")
    daily_log = daily_log if isinstance(daily_log, dict) else None
    office_mode_missing = not bool(str((daily_log or {}).get("OfficeMode") or "").strip())
    period_reminder = _period_cycle_awareness(today_summary, historical_days, local_today)
    if not office_mode_missing and not period_reminder:
        return None
    awareness: dict[str, Any] = {"NeedsLogging": True}
    notices = []
    if office_mode_missing and local_today.weekday() < 5:
        awareness["OfficeMode"] = {"NeedsLogging": True, "LogDate": local_today.isoformat()}
        notices.append("Today's work location is not logged. Work with the user to record Office, WFH, or Other.")
    if period_reminder:
        awareness["Period"] = period_reminder
        notices.append(period_reminder["AgentNotice"])
    if not notices:
        return None
    awareness["AgentNotice"] = " ".join(notices)
    return awareness


def _resting_heart_rate_awareness(
    items: list[dict[str, Any]],
    local_today: date,
    external_subject: str,
) -> dict[str, Any] | None:
    latest: tuple[date, int] | None = None
    for item in items:
        try:
            log_date = date.fromisoformat(str(item.get("LogDate") or ""))
            heart_rate = int(item.get("RestingHeartRate"))
        except (TypeError, ValueError):
            continue
        if latest is None or log_date > latest[0]:
            latest = (log_date, heart_rate)
    if latest is None:
        return None
    log_date, heart_rate = latest
    if heart_rate < RESTING_HEART_RATE_ALERT_THRESHOLD or (local_today - log_date).days > RESTING_HEART_RATE_ALERT_MAX_AGE_DAYS:
        return None
    alert_key = f"resting-heart-rate:{log_date.isoformat()}:{heart_rate}"
    if not _claim_agent_alert(external_subject, alert_key):
        return None
    return {
        "Flagged": True,
        "LogDate": log_date.isoformat(),
        "RestingHeartRate": heart_rate,
        "Threshold": RESTING_HEART_RATE_ALERT_THRESHOLD,
        "AgentNotice": (
            f"A resting heart-rate reading of {heart_rate} bpm was flagged for {log_date.isoformat()}. "
            "Keep illness, medication, sleep, and hydration context in mind; this is a trend cue, not a diagnosis."
        ),
    }


def _task_awareness(headers: Any) -> dict[str, Any] | None:
    try:
        principal = _require_principal(headers)
        access_token, account = _refresh_access_for_principal(principal)
        headers_with_token = _authorized_headers(access_token)
        context = _http_json("GET", "/api/integrations/health-mcp/context", headers=headers_with_token)
        tasks_payload = _http_json("GET", "/api/tasks?view=open", headers=headers_with_token)
        weight_payload = _http_json("GET", "/api/integrations/health-mcp/weight-trend?days=365", headers=headers_with_token)
        tasks = tasks_payload.get("Tasks")
        if not isinstance(tasks, list):
            return None
        reminder_timezone = str(context.get("ReminderTimeZone") or "UTC")
        now_utc = _utc_now()
        local_today = now_utc.astimezone(_task_timezone(None, reminder_timezone)).date()
        measurements_payload = _http_json(
            "GET",
            (
                "/api/integrations/health-mcp/measurements"
                f"?start_date={(local_today - timedelta(days=RESTING_HEART_RATE_ALERT_MAX_AGE_DAYS)).isoformat()}"
                f"&end_date={local_today.isoformat()}&limit=10"
            ),
            headers=headers_with_token,
        )
        today_summary = _http_json(
            "GET", f"/api/integrations/health-mcp/summary?date={local_today.isoformat()}", headers=headers_with_token
        )
        previous_summary = _http_json(
            "GET",
            f"/api/integrations/health-mcp/summary?date={(local_today - timedelta(days=1)).isoformat()}",
            headers=headers_with_token,
        )
        history_payload = _http_json(
            "GET",
            (
                "/api/integrations/health-mcp/history?history_type=days"
                f"&start_date={(local_today - timedelta(days=365)).isoformat()}&end_date={local_today.isoformat()}&limit=365"
            ),
            headers=headers_with_token,
        )
        awareness = _health_task_items(
            [task for task in tasks if isinstance(task, dict)],
            int(account["everday_user_id"]),
            reminder_timezone,
            now_utc,
        )
        weekly_review = _weekly_review_awareness(reminder_timezone, now_utc)
        if weekly_review:
            awareness["WeeklyReview"] = weekly_review
            awareness["AgentNotice"] = ". ".join(
                notice for notice in (awareness["AgentNotice"], weekly_review["AgentNotice"]) if notice
            )
        weight_items = weight_payload.get("Items")
        weight_reminder = _weight_logging_awareness(
            [item for item in weight_items if isinstance(item, dict)] if isinstance(weight_items, list) else [],
            reminder_timezone,
            now_utc,
        )
        if weight_reminder["NeedsLogging"]:
            awareness["WeightReminder"] = weight_reminder
            if weight_reminder["LastLoggedDate"]:
                weight_notice = (
                    f"Weight has not been logged for {weight_reminder['DaysSinceLogged']} days "
                    f"(last logged {weight_reminder['LastLoggedDate']})."
                )
            else:
                weight_notice = "No weight has been logged in the last 365 days."
            awareness["AgentNotice"] = ". ".join(
                notice for notice in (awareness["AgentNotice"], weight_notice) if notice
            )
        measurement_items = measurements_payload.get("Items")
        resting_heart_rate_alert = _resting_heart_rate_awareness(
            [item for item in measurement_items if isinstance(item, dict)] if isinstance(measurement_items, list) else [],
            local_today,
            str(principal["subject"]),
        )
        if resting_heart_rate_alert:
            awareness["RestingHeartRateAlert"] = resting_heart_rate_alert
            awareness["AgentNotice"] = ". ".join(
                notice for notice in (awareness["AgentNotice"], resting_heart_rate_alert["AgentNotice"]) if notice
            )
        notes_reminder = _dashboard_notes_awareness(today_summary, previous_summary, local_today)
        if notes_reminder:
            awareness["DashboardNotesReminder"] = notes_reminder
            awareness["AgentNotice"] = ". ".join(
                notice for notice in (awareness["AgentNotice"], notes_reminder["AgentNotice"]) if notice
            )
        dinner_reflection = _dinner_reflection_awareness(today_summary, local_today)
        if dinner_reflection:
            awareness["DinnerReflectionReminder"] = dinner_reflection
            awareness["AgentNotice"] = ". ".join(
                notice for notice in (awareness["AgentNotice"], dinner_reflection["AgentNotice"]) if notice
            )
        historical_days = history_payload.get("Items")
        daily_details_reminder = _daily_details_awareness(
            today_summary,
            [item for item in historical_days if isinstance(item, dict)] if isinstance(historical_days, list) else [],
            local_today,
        )
        if daily_details_reminder:
            awareness["DailyDetailsReminder"] = daily_details_reminder
            awareness["AgentNotice"] = ". ".join(
                notice for notice in (awareness["AgentNotice"], daily_details_reminder["AgentNotice"]) if notice
            )
        return awareness
    except (KeyError, TypeError, ValueError, RuntimeError):
        return None


def _everday_profile(access_token: str) -> dict[str, Any]:
    return _http_json(
        "GET",
        "/api/health/settings/profile",
        headers=_authorized_headers(access_token),
    )


def _require_principal(headers: Any) -> dict[str, str | None]:
    subject = (headers.get("X-Auth-Request-Sub") or "").strip()
    email = (headers.get("X-Auth-Request-Email") or "").strip() or None
    name = (headers.get("X-Auth-Request-Name") or "").strip() or None
    if not subject:
        preferred = (headers.get("X-Auth-Request-Preferred-Username") or "").strip()
        if preferred:
            subject = preferred
    if not subject:
        raise ValueError("Missing authenticated subject headers from the OAuth gateway.")
    return {"subject": subject, "email": email, "name": name}


def _require_linked_account(principal: dict[str, str | None]) -> sqlite3.Row:
    account = _load_account(principal["subject"] or "")
    if account is None:
        raise ValueError(
            "No Everday account is linked for this MCP identity. Run connect_account first."
        )
    return account


def _refresh_access_for_principal(principal: dict[str, str | None]) -> tuple[str, sqlite3.Row]:
    account = _require_linked_account(principal)
    refresh_token = _decrypt(account["refresh_token_ciphertext"])
    refreshed = _everday_refresh(refresh_token)
    new_refresh = str(refreshed.get("RefreshToken") or "").strip()
    access_token = str(refreshed.get("AccessToken") or "").strip()
    if not access_token or not new_refresh:
        raise RuntimeError("Everday refresh response was missing tokens.")
    _replace_refresh_token(principal["subject"] or "", new_refresh)
    account = _require_linked_account(principal)
    return access_token, account


def _tool_connect_account(arguments: dict[str, Any], headers: Any) -> dict[str, Any]:
    principal = _require_principal(headers)
    username = str(arguments.get("username") or "").strip()
    password = str(arguments.get("password") or "")
    if not username or not password:
        raise ValueError("username and password are required.")
    login = _everday_login(username, password)
    access_token = str(login.get("AccessToken") or "").strip()
    refresh_token = str(login.get("RefreshToken") or "").strip()
    if not access_token or not refresh_token:
        raise RuntimeError("Everday login did not return usable tokens.")
    profile = _everday_profile(access_token)
    everday_user_id = int(profile.get("UserId"))
    _save_account(
        principal["subject"] or "",
        principal.get("email"),
        principal.get("name"),
        everday_user_id,
        username,
        refresh_token,
    )
    return {
        "status": "linked",
        "external_subject": principal["subject"],
        "external_email": principal.get("email"),
        "everday_user_id": everday_user_id,
        "everday_username": username,
        "profile": profile,
    }


def _tool_start_account_link(arguments: dict[str, Any], headers: Any) -> dict[str, Any]:
    principal = _require_principal(headers)
    session = _create_link_session(principal)
    return {
        "status": "pending_browser_link",
        "external_subject": principal["subject"],
        "external_email": principal.get("email"),
        "link_url": session["link_url"],
        "expires_at": session["expires_at"],
        "instructions": (
            "Open the link_url in your browser, sign into Everday on the hosted page, "
            "then return here and run connection_status."
        ),
    }


def _tool_disconnect_account(arguments: dict[str, Any], headers: Any) -> dict[str, Any]:
    principal = _require_principal(headers)
    account = _require_linked_account(principal)
    _revoke_account(principal["subject"] or "")
    return {
        "status": "disconnected",
        "external_subject": principal["subject"],
        "everday_user_id": account["everday_user_id"],
        "everday_username": account["everday_username"],
    }


def _tool_connection_status(arguments: dict[str, Any], headers: Any) -> dict[str, Any]:
    principal = _require_principal(headers)
    account = _load_account(principal["subject"] or "")
    pending_link = _load_pending_link_session(principal["subject"] or "")
    if account is None:
        result = {
            "linked": False,
            "external_subject": principal["subject"],
            "external_email": principal.get("email"),
        }
        if pending_link is not None and _parse_iso_datetime(pending_link["expires_at"]) > _utc_now():
            link_url = (
                f"{Config.public_base_url}/link/{pending_link['session_token']}"
                if Config.public_base_url
                else f"/link/{pending_link['session_token']}"
            )
            result["pending_link"] = {
                "status": pending_link["status"],
                "link_url": link_url,
                "expires_at": pending_link["expires_at"],
            }
        return result
    result = {
        "linked": True,
        "external_subject": principal["subject"],
        "external_email": principal.get("email"),
        "everday_user_id": account["everday_user_id"],
        "everday_username": account["everday_username"],
        "created_at": account["created_at"],
        "updated_at": account["updated_at"],
    }
    if pending_link is not None and _parse_iso_datetime(pending_link["expires_at"]) > _utc_now():
        link_url = (
            f"{Config.public_base_url}/link/{pending_link['session_token']}"
            if Config.public_base_url
            else f"/link/{pending_link['session_token']}"
        )
        result["pending_link"] = {
            "status": pending_link["status"],
            "link_url": link_url,
            "expires_at": pending_link["expires_at"],
        }
    return result


def _tool_log_meal_text(arguments: dict[str, Any], headers: Any) -> dict[str, Any]:
    principal = _require_principal(headers)
    access_token, _account = _refresh_access_for_principal(principal)
    text = str(arguments.get("text") or "").strip()
    if not text:
        raise ValueError("text is required.")
    payload = {
        "Date": str(arguments.get("date") or _today_iso()),
        "MealType": arguments.get("meal_type"),
        "Note": arguments.get("note"),
        "Text": text,
        "ImageBase64": None,
    }
    return _http_json(
        "POST",
        "/api/integrations/health-mcp/log-meal",
        payload=payload,
        headers=_authorized_headers(access_token),
    )


def _tool_log_meal_image(arguments: dict[str, Any], headers: Any) -> dict[str, Any]:
    principal = _require_principal(headers)
    access_token, _account = _refresh_access_for_principal(principal)
    image_base64 = str(arguments.get("image_base64") or "").strip()
    if not image_base64:
        raise ValueError("image_base64 is required.")
    payload = {
        "Date": str(arguments.get("date") or _today_iso()),
        "MealType": arguments.get("meal_type"),
        "Note": arguments.get("note"),
        "Text": None,
        "ImageBase64": image_base64,
    }
    return _http_json(
        "POST",
        "/api/integrations/health-mcp/log-meal",
        payload=payload,
        headers=_authorized_headers(access_token),
    )


def _tool_log_meal_manual(arguments: dict[str, Any], headers: Any) -> dict[str, Any]:
    principal = _require_principal(headers)
    access_token, _account = _refresh_access_for_principal(principal)
    food_name = str(arguments.get("food_name") or "").strip()
    if not food_name:
        raise ValueError("food_name is required.")
    if "calories" not in arguments:
        raise ValueError("calories is required.")
    payload = {
        "Date": str(arguments.get("date") or _today_iso()),
        "MealType": arguments.get("meal_type"),
        "Note": arguments.get("note"),
        "FoodName": food_name,
        "CaloriesPerServing": float(arguments["calories"]),
        "ProteinPerServing": float(arguments.get("protein") or 0),
        "FibrePerServing": arguments.get("fibre"),
        "CarbsPerServing": arguments.get("carbs"),
        "FatPerServing": arguments.get("fat"),
        "SaturatedFatPerServing": arguments.get("saturated_fat"),
        "SugarPerServing": arguments.get("sugar"),
        "SodiumPerServing": arguments.get("sodium"),
        "ServingQuantity": float(arguments.get("serving_quantity") or 1.0),
        "ServingUnit": str(arguments.get("serving_unit") or "serving"),
    }
    return _http_json(
        "POST",
        "/api/integrations/health-mcp/log-meal-manual",
        payload=payload,
        headers=_authorized_headers(access_token),
    )


def _tool_update_meal(arguments: dict[str, Any], headers: Any) -> dict[str, Any]:
    principal = _require_principal(headers)
    access_token, _account = _refresh_access_for_principal(principal)
    meal_entry_id = str(arguments.get("meal_entry_id") or "").strip()
    if not meal_entry_id:
        raise ValueError("meal_entry_id is required.")
    payload = {
        "MealEntryId": meal_entry_id,
        "Date": arguments.get("date"),
        "MealType": arguments.get("meal_type"),
        "Note": arguments.get("note"),
        "Quantity": arguments.get("quantity"),
        "FoodName": arguments.get("food_name"),
        "CaloriesPerServing": arguments.get("calories"),
        "ProteinPerServing": arguments.get("protein"),
        "FibrePerServing": arguments.get("fibre"),
        "CarbsPerServing": arguments.get("carbs"),
        "FatPerServing": arguments.get("fat"),
        "SaturatedFatPerServing": arguments.get("saturated_fat"),
        "SugarPerServing": arguments.get("sugar"),
        "SodiumPerServing": arguments.get("sodium"),
        "ServingQuantity": arguments.get("serving_quantity"),
        "ServingUnit": arguments.get("serving_unit"),
    }
    return _http_json(
        "POST",
        "/api/integrations/health-mcp/update-meal",
        payload=payload,
        headers=_authorized_headers(access_token),
    )


def _tool_delete_meal(arguments: dict[str, Any], headers: Any) -> dict[str, Any]:
    principal = _require_principal(headers)
    access_token, _account = _refresh_access_for_principal(principal)
    meal_entry_id = str(arguments.get("meal_entry_id") or "").strip()
    if not meal_entry_id:
        raise ValueError("meal_entry_id is required.")
    path = f"/api/integrations/health-mcp/meal/{urllib.parse.quote(meal_entry_id)}"
    return _http_json("DELETE", path, headers=_authorized_headers(access_token))


def _tool_log_weight(arguments: dict[str, Any], headers: Any) -> dict[str, Any]:
    principal = _require_principal(headers)
    access_token, _account = _refresh_access_for_principal(principal)
    if "weight_kg" not in arguments:
        raise ValueError("weight_kg is required.")
    payload = {
        "Date": str(arguments.get("date") or _today_iso()),
        "WeightKg": float(arguments["weight_kg"]),
    }
    return _http_json(
        "POST",
        "/api/integrations/health-mcp/log-weight",
        payload=payload,
        headers=_authorized_headers(access_token),
    )


def _tool_update_daily_log(arguments: dict[str, Any], headers: Any) -> dict[str, Any]:
    principal = _require_principal(headers)
    access_token, _account = _refresh_access_for_principal(principal)
    payload = {"Date": str(arguments.get("date") or _today_iso())}
    field_map = {
        "steps": "Steps",
        "step_kcal_factor_override": "StepKcalFactorOverride",
        "weight_kg": "WeightKg",
        "office_mode": "OfficeMode",
        "water_litres": "WaterLitres",
        "walking_pad_minutes": "WalkingPadMinutes",
        "exercise_notes": "ExerciseNotes",
        "sleep_hours": "SleepHours",
        "period": "Period",
        "period_label": "PeriodLabel",
        "hunger_before_dinner": "HungerBeforeDinner",
        "overall_satisfaction": "OverallSatisfaction",
        "takeaway": "Takeaway",
        "logged_complete": "LoggedComplete",
        "adherent_day": "AdherentDay",
        "adherent_status": "AdherentStatus",
        "notes": "Notes",
        "daily_calorie_target_snapshot": "DailyCalorieTargetSnapshot",
        "protein_target_snapshot": "ProteinTargetSnapshot",
        "step_target_snapshot": "StepTargetSnapshot",
    }
    for argument_name, payload_name in field_map.items():
        if argument_name in arguments:
            payload[payload_name] = arguments.get(argument_name)
    if len(payload) == 1:
        raise ValueError("Provide at least one daily log field to update.")
    return _http_json(
        "POST",
        "/api/integrations/health-mcp/daily-log",
        payload=payload,
        headers=_authorized_headers(access_token),
    )


def _tool_log_workout(arguments: dict[str, Any], headers: Any) -> dict[str, Any]:
    principal = _require_principal(headers)
    access_token, _account = _refresh_access_for_principal(principal)
    workout_type = str(arguments.get("workout_type") or "").strip()
    workout_name = str(arguments.get("workout_name") or "").strip()
    if not workout_type or not workout_name:
        raise ValueError("workout_type and workout_name are required.")
    payload = {
        "Date": str(arguments.get("date") or _today_iso()),
        "WorkoutType": workout_type,
        "WorkoutName": workout_name,
        "DurationMinutes": arguments.get("duration_minutes"),
        "CaloriesBurned": int(arguments.get("calories_burned") or 0),
        "DistanceKm": arguments.get("distance_km"),
        "StartedAt": arguments.get("started_at"),
        "EndedAt": arguments.get("ended_at"),
        "Notes": arguments.get("notes"),
    }
    return _http_json(
        "POST",
        "/api/integrations/health-mcp/log-workout",
        payload=payload,
        headers=_authorized_headers(access_token),
    )


def _tool_update_workout(arguments: dict[str, Any], headers: Any) -> dict[str, Any]:
    principal = _require_principal(headers)
    access_token, _account = _refresh_access_for_principal(principal)
    workout_id = str(arguments.get("workout_id") or "").strip()
    if not workout_id:
        raise ValueError("workout_id is required.")
    payload = {
        "WorkoutId": workout_id,
        "Date": arguments.get("date"),
        "WorkoutType": arguments.get("workout_type"),
        "WorkoutName": arguments.get("workout_name"),
        "DurationMinutes": arguments.get("duration_minutes"),
        "CaloriesBurned": arguments.get("calories_burned"),
        "DistanceKm": arguments.get("distance_km"),
        "StartedAt": arguments.get("started_at"),
        "EndedAt": arguments.get("ended_at"),
        "Notes": arguments.get("notes"),
    }
    return _http_json(
        "POST",
        "/api/integrations/health-mcp/update-workout",
        payload=payload,
        headers=_authorized_headers(access_token),
    )


def _tool_delete_workout(arguments: dict[str, Any], headers: Any) -> dict[str, Any]:
    principal = _require_principal(headers)
    access_token, _account = _refresh_access_for_principal(principal)
    workout_id = str(arguments.get("workout_id") or "").strip()
    if not workout_id:
        raise ValueError("workout_id is required.")
    path = f"/api/integrations/health-mcp/workout/{urllib.parse.quote(workout_id)}"
    return _http_json("DELETE", path, headers=_authorized_headers(access_token))


def _tool_get_today_summary(arguments: dict[str, Any], headers: Any) -> dict[str, Any]:
    principal = _require_principal(headers)
    access_token, _account = _refresh_access_for_principal(principal)
    date_value = str(arguments.get("date") or _today_iso())
    path = f"/api/integrations/health-mcp/summary?{urllib.parse.urlencode({'date': date_value})}"
    return _http_json("GET", path, headers=_authorized_headers(access_token))


def _tool_get_connection_context(arguments: dict[str, Any], headers: Any) -> dict[str, Any]:
    principal = _require_principal(headers)
    access_token, account = _refresh_access_for_principal(principal)
    context = _http_json("GET", "/api/integrations/health-mcp/context", headers=_authorized_headers(access_token))
    context["external_subject"] = principal["subject"]
    context["external_email"] = principal.get("email")
    context["everday_user_id"] = int(account["everday_user_id"])
    context["everday_username"] = account["everday_username"]
    context["server_date"] = _today_iso()
    return context


def _tool_get_daily_log_fields(arguments: dict[str, Any], headers: Any) -> dict[str, Any]:
    context = _tool_get_connection_context(arguments, headers)
    return {
        "DailyLogFields": context.get("DailyLogFields", []),
        "ReminderTimeZone": context.get("ReminderTimeZone"),
        "server_date": context.get("server_date"),
    }


def _tool_get_meal_type_options(arguments: dict[str, Any], headers: Any) -> dict[str, Any]:
    _require_principal(headers)
    return {"MealSlots": MEAL_SLOT_OPTIONS}


def _tool_get_history_type_options(arguments: dict[str, Any], headers: Any) -> dict[str, Any]:
    _require_principal(headers)
    return {"HistoryTypes": HISTORY_TYPE_OPTIONS}


def _tool_get_workout_type_options(arguments: dict[str, Any], headers: Any) -> dict[str, Any]:
    _require_principal(headers)
    return {"WorkoutTypes": WORKOUT_TYPE_OPTIONS}


def _tool_get_weight_trend(arguments: dict[str, Any], headers: Any) -> dict[str, Any]:
    principal = _require_principal(headers)
    access_token, _account = _refresh_access_for_principal(principal)
    params = {"days": int(arguments.get("days") or 14)}
    path = f"/api/integrations/health-mcp/weight-trend?{urllib.parse.urlencode(params)}"
    return _http_json("GET", path, headers=_authorized_headers(access_token))


def _tool_get_step_summary(arguments: dict[str, Any], headers: Any) -> dict[str, Any]:
    principal = _require_principal(headers)
    access_token, _account = _refresh_access_for_principal(principal)
    start_date = str(arguments.get("start_date") or "").strip()
    end_date = str(arguments.get("end_date") or "").strip()
    if not start_date or not end_date:
        raise ValueError("start_date and end_date are required.")
    path = f"/api/integrations/health-mcp/step-summary?{urllib.parse.urlencode({'start_date': start_date, 'end_date': end_date})}"
    return _http_json("GET", path, headers=_authorized_headers(access_token))


def _tool_get_targets_history(arguments: dict[str, Any], headers: Any) -> dict[str, Any]:
    principal = _require_principal(headers)
    access_token, _account = _refresh_access_for_principal(principal)
    params = {"limit": int(arguments.get("limit") or 20)}
    path = f"/api/integrations/health-mcp/targets/history?{urllib.parse.urlencode(params)}"
    return _http_json("GET", path, headers=_authorized_headers(access_token))


def _tool_save_food_from_meal(arguments: dict[str, Any], headers: Any) -> dict[str, Any]:
    principal = _require_principal(headers)
    access_token, _account = _refresh_access_for_principal(principal)
    meal_entry_id = str(arguments.get("meal_entry_id") or "").strip()
    if not meal_entry_id:
        raise ValueError("meal_entry_id is required.")
    payload = {
        "MealEntryId": meal_entry_id,
        "FoodName": arguments.get("food_name"),
    }
    return _http_json(
        "POST",
        "/api/integrations/health-mcp/foods/from-meal",
        payload=payload,
        headers=_authorized_headers(access_token),
    )


def _tool_get_meal(arguments: dict[str, Any], headers: Any) -> dict[str, Any]:
    principal = _require_principal(headers)
    access_token, _account = _refresh_access_for_principal(principal)
    meal_entry_id = str(arguments.get("meal_entry_id") or "").strip()
    if not meal_entry_id:
        raise ValueError("meal_entry_id is required.")
    path = f"/api/integrations/health-mcp/meal/{urllib.parse.quote(meal_entry_id)}"
    return _http_json("GET", path, headers=_authorized_headers(access_token))


def _tool_get_today_meals(arguments: dict[str, Any], headers: Any) -> dict[str, Any]:
    principal = _require_principal(headers)
    access_token, _account = _refresh_access_for_principal(principal)
    date_value = str(arguments.get("date") or _today_iso())
    path = f"/api/integrations/health-mcp/meals/today?{urllib.parse.urlencode({'date': date_value})}"
    return _http_json("GET", path, headers=_authorized_headers(access_token))


def _tool_search_meals(arguments: dict[str, Any], headers: Any) -> dict[str, Any]:
    principal = _require_principal(headers)
    access_token, _account = _refresh_access_for_principal(principal)
    query = str(arguments.get("query") or "").strip()
    if not query:
        raise ValueError("query is required.")
    params = {"query": query, "limit": int(arguments.get("limit") or 50)}
    if arguments.get("date"):
        params["date"] = str(arguments.get("date"))
    path = f"/api/integrations/health-mcp/meals/search?{urllib.parse.urlencode(params)}"
    return _http_json("GET", path, headers=_authorized_headers(access_token))


def _tool_get_goals(arguments: dict[str, Any], headers: Any) -> dict[str, Any]:
    summary = _tool_get_today_summary(arguments, headers)
    targets = summary.get("Targets")
    if not isinstance(targets, dict):
        raise RuntimeError("Everday summary did not return Targets.")
    return {
        "Date": str(arguments.get("date") or _today_iso()),
        "Targets": targets,
    }


def _tool_update_targets(arguments: dict[str, Any], headers: Any) -> dict[str, Any]:
    principal = _require_principal(headers)
    access_token, _account = _refresh_access_for_principal(principal)
    field_map = {
        "daily_calorie_target": "DailyCalorieTarget",
        "protein_target_min": "ProteinTargetMin",
        "protein_target_max": "ProteinTargetMax",
        "step_target": "StepTarget",
        "step_kcal_factor": "StepKcalFactor",
        "fibre_target": "FibreTarget",
        "carbs_target": "CarbsTarget",
        "fat_target": "FatTarget",
        "saturated_fat_target": "SaturatedFatTarget",
        "sugar_target": "SugarTarget",
        "sodium_target": "SodiumTarget",
    }
    payload = {
        payload_name: arguments[argument_name]
        for argument_name, payload_name in field_map.items()
        if argument_name in arguments
    }
    if not payload:
        raise ValueError("Provide at least one target to update.")
    result = _http_json(
        "PUT",
        "/api/health/settings",
        payload=payload,
        headers=_authorized_headers(access_token),
    )
    targets = result.get("Targets")
    if not isinstance(targets, dict):
        raise RuntimeError("Everday settings response did not contain Targets.")
    return {"Targets": targets}


def _tool_get_history(arguments: dict[str, Any], headers: Any) -> dict[str, Any]:
    principal = _require_principal(headers)
    access_token, _account = _refresh_access_for_principal(principal)
    history_type = str(arguments.get("history_type") or "").strip()
    start_date = str(arguments.get("start_date") or "").strip()
    end_date = str(arguments.get("end_date") or "").strip()
    if not history_type or not start_date or not end_date:
        raise ValueError("history_type, start_date, and end_date are required.")
    params = {
        "history_type": history_type,
        "start_date": start_date,
        "end_date": end_date,
        "limit": int(arguments.get("limit") or 200),
    }
    path = f"/api/integrations/health-mcp/history?{urllib.parse.urlencode(params)}"
    return _http_json("GET", path, headers=_authorized_headers(access_token))


def _tool_get_meal_slots(arguments: dict[str, Any], headers: Any) -> dict[str, Any]:
    _require_principal(headers)
    return {"MealSlots": MEAL_SLOT_OPTIONS}


def _tool_get_history_types(arguments: dict[str, Any], headers: Any) -> dict[str, Any]:
    _require_principal(headers)
    return {"HistoryTypes": HISTORY_TYPE_OPTIONS}


def _tool_search_saved_foods(arguments: dict[str, Any], headers: Any) -> dict[str, Any]:
    principal = _require_principal(headers)
    access_token, _account = _refresh_access_for_principal(principal)
    query = str(arguments.get("query") or "").strip()
    if not query:
        raise ValueError("query is required.")
    params = {
        "q": query,
        "limit": int(arguments.get("limit") or 50),
    }
    path = f"/api/integrations/health-mcp/foods?{urllib.parse.urlencode(params)}"
    return _http_json("GET", path, headers=_authorized_headers(access_token))


def _tool_get_saved_foods(arguments: dict[str, Any], headers: Any) -> dict[str, Any]:
    principal = _require_principal(headers)
    access_token, _account = _refresh_access_for_principal(principal)
    params = {"limit": int(arguments.get("limit") or 200)}
    path = f"/api/integrations/health-mcp/foods?{urllib.parse.urlencode(params)}"
    return _http_json("GET", path, headers=_authorized_headers(access_token))


def _tool_upsert_recipe_review(arguments: dict[str, Any], headers: Any) -> dict[str, Any]:
    principal = _require_principal(headers)
    access_token, _account = _refresh_access_for_principal(principal)
    recipe_name = str(arguments.get("recipe_name") or "").strip()
    log_date = str(arguments.get("log_date") or "").strip()
    recipe_review_id = str(arguments.get("recipe_review_id") or "").strip()
    if not recipe_review_id and (not recipe_name or not log_date):
        raise ValueError("recipe_name and log_date are required when recipe_review_id is not provided.")
    payload = {
        "RecipeReviewId": recipe_review_id or None,
        "RecipeName": recipe_name or None,
        "LogDate": log_date or None,
        "MealEntryId": arguments.get("meal_entry_id"),
        "Rating": arguments.get("rating"),
        "WouldMakeAgain": arguments.get("would_make_again"),
        "HallOfFameOverride": arguments.get("hall_of_fame_override"),
        "Notes": arguments.get("notes"),
    }
    return _http_json(
        "POST",
        "/api/integrations/health-mcp/recipe-reviews",
        payload=payload,
        headers=_authorized_headers(access_token),
    )


def _tool_get_recipe_reviews(arguments: dict[str, Any], headers: Any) -> dict[str, Any]:
    principal = _require_principal(headers)
    access_token, _account = _refresh_access_for_principal(principal)
    params = {"limit": int(arguments.get("limit") or 200)}
    path = f"/api/integrations/health-mcp/recipe-reviews?{urllib.parse.urlencode(params)}"
    return _http_json("GET", path, headers=_authorized_headers(access_token))


def _tool_get_recipe_stats(arguments: dict[str, Any], headers: Any) -> dict[str, Any]:
    principal = _require_principal(headers)
    access_token, _account = _refresh_access_for_principal(principal)
    params = {"limit": int(arguments.get("limit") or 100)}
    if arguments.get("start_date"):
        params["start_date"] = str(arguments.get("start_date"))
    if arguments.get("end_date"):
        params["end_date"] = str(arguments.get("end_date"))
    path = f"/api/integrations/health-mcp/recipe-stats?{urllib.parse.urlencode(params)}"
    return _http_json("GET", path, headers=_authorized_headers(access_token))


def _tool_upsert_product_review(arguments: dict[str, Any], headers: Any) -> dict[str, Any]:
    principal = _require_principal(headers)
    access_token, _account = _refresh_access_for_principal(principal)
    product_name = str(arguments.get("product_name") or "").strip()
    if not product_name:
        raise ValueError("product_name is required.")
    payload = {
        "FoodId": arguments.get("food_id"),
        "ProductName": product_name,
        "Brand": arguments.get("brand"),
        "Category": arguments.get("category"),
        "BuyAgain": arguments.get("buy_again"),
        "Rating": arguments.get("rating"),
        "CaloriesPerServing": arguments.get("calories_per_serving"),
        "ProteinPerServing": arguments.get("protein_per_serving"),
        "Notes": arguments.get("notes"),
    }
    return _http_json(
        "POST",
        "/api/integrations/health-mcp/product-reviews",
        payload=payload,
        headers=_authorized_headers(access_token),
    )


def _tool_get_product_reviews(arguments: dict[str, Any], headers: Any) -> dict[str, Any]:
    principal = _require_principal(headers)
    access_token, _account = _refresh_access_for_principal(principal)
    params = {"limit": int(arguments.get("limit") or 200)}
    path = f"/api/integrations/health-mcp/product-reviews?{urllib.parse.urlencode(params)}"
    return _http_json("GET", path, headers=_authorized_headers(access_token))


def _tool_upsert_experiment(arguments: dict[str, Any], headers: Any) -> dict[str, Any]:
    principal = _require_principal(headers)
    access_token, _account = _refresh_access_for_principal(principal)
    start_date = str(arguments.get("start_date") or "").strip()
    variable_changed = str(arguments.get("variable_changed") or "").strip()
    if not start_date or not variable_changed:
        raise ValueError("start_date and variable_changed are required.")
    payload = {
        "ExperimentId": arguments.get("experiment_id"),
        "StartDate": start_date,
        "EndDate": arguments.get("end_date"),
        "VariableChanged": variable_changed,
        "Reason": arguments.get("reason"),
        "ExpectedOutcome": arguments.get("expected_outcome"),
        "ActualOutcome": arguments.get("actual_outcome"),
        "Decision": arguments.get("decision"),
        "Status": arguments.get("status") or "In progress",
    }
    return _http_json(
        "POST",
        "/api/integrations/health-mcp/experiments",
        payload=payload,
        headers=_authorized_headers(access_token),
    )


def _tool_get_experiments(arguments: dict[str, Any], headers: Any) -> dict[str, Any]:
    principal = _require_principal(headers)
    access_token, _account = _refresh_access_for_principal(principal)
    params = {"limit": int(arguments.get("limit") or 200)}
    path = f"/api/integrations/health-mcp/experiments?{urllib.parse.urlencode(params)}"
    return _http_json("GET", path, headers=_authorized_headers(access_token))


def _tool_upsert_measurement(arguments: dict[str, Any], headers: Any) -> dict[str, Any]:
    principal = _require_principal(headers)
    access_token, _account = _refresh_access_for_principal(principal)
    log_date = str(arguments.get("log_date") or "").strip()
    if not log_date:
        raise ValueError("log_date is required.")
    payload = {
        "LogDate": log_date,
        "WaistCm": arguments.get("waist_cm"),
        "HipsCm": arguments.get("hips_cm"),
        "RestingHeartRate": arguments.get("resting_heart_rate"),
        "PeriodCycleNotes": arguments.get("period_cycle_notes"),
        "Notes": arguments.get("notes"),
    }
    return _http_json(
        "POST",
        "/api/integrations/health-mcp/measurements",
        payload=payload,
        headers=_authorized_headers(access_token),
    )


def _tool_get_measurements(arguments: dict[str, Any], headers: Any) -> dict[str, Any]:
    principal = _require_principal(headers)
    access_token, _account = _refresh_access_for_principal(principal)
    params = {"limit": int(arguments.get("limit") or 200)}
    if arguments.get("start_date"):
        params["start_date"] = str(arguments.get("start_date"))
    if arguments.get("end_date"):
        params["end_date"] = str(arguments.get("end_date"))
    path = f"/api/integrations/health-mcp/measurements?{urllib.parse.urlencode(params)}"
    return _http_json("GET", path, headers=_authorized_headers(access_token))


def _tool_upsert_weekly_review_note(arguments: dict[str, Any], headers: Any) -> dict[str, Any]:
    principal = _require_principal(headers)
    access_token, _account = _refresh_access_for_principal(principal)
    week_start = str(arguments.get("week_start") or "").strip()
    if not week_start:
        raise ValueError("week_start is required.")
    payload = {
        "WeekStart": week_start,
        "BiggestNutritionWin": arguments.get("biggest_nutrition_win"),
        "ImprovementForNextWeek": arguments.get("improvement_for_next_week"),
    }
    return _http_json(
        "POST",
        "/api/integrations/health-mcp/weekly-review-note",
        payload=payload,
        headers=_authorized_headers(access_token),
    )


def _tool_get_weekly_review_note(arguments: dict[str, Any], headers: Any) -> dict[str, Any]:
    principal = _require_principal(headers)
    access_token, _account = _refresh_access_for_principal(principal)
    week_start = str(arguments.get("week_start") or "").strip()
    if not week_start:
        raise ValueError("week_start is required.")
    path = f"/api/integrations/health-mcp/weekly-review-note?{urllib.parse.urlencode({'week_start': week_start})}"
    return _http_json("GET", path, headers=_authorized_headers(access_token))


def _tool_get_weekly_review(arguments: dict[str, Any], headers: Any) -> dict[str, Any]:
    principal = _require_principal(headers)
    access_token, _account = _refresh_access_for_principal(principal)
    week_start = str(arguments.get("week_start") or "").strip()
    if not week_start:
        raise ValueError("week_start is required.")
    path = f"/api/integrations/health-mcp/weekly-review?{urllib.parse.urlencode({'week_start': week_start})}"
    return _http_json("GET", path, headers=_authorized_headers(access_token))


def _tool_get_insight_type_options(arguments: dict[str, Any], headers: Any) -> dict[str, Any]:
    _require_principal(headers)
    return {
        "InsightTypes": INSIGHT_TYPE_OPTIONS,
        "PeriodTypes": INSIGHT_PERIOD_OPTIONS,
        "StatusValues": INSIGHT_STATUS_ENUM,
    }


def _tool_upsert_insight(arguments: dict[str, Any], headers: Any) -> dict[str, Any]:
    principal = _require_principal(headers)
    access_token, _account = _refresh_access_for_principal(principal)
    insight_type = str(arguments.get("insight_type") or "").strip()
    period_type = str(arguments.get("period_type") or "").strip()
    period_start = str(arguments.get("period_start") or "").strip()
    title = str(arguments.get("title") or "").strip()
    if not insight_type or not period_type or not period_start or not title:
        raise ValueError("insight_type, period_type, period_start, and title are required.")
    payload = {
        "InsightId": arguments.get("insight_id"),
        "InsightType": insight_type,
        "PeriodType": period_type,
        "PeriodStart": period_start,
        "PeriodEnd": arguments.get("period_end"),
        "Title": title,
        "Summary": arguments.get("summary"),
        "Confidence": arguments.get("confidence"),
        "Status": arguments.get("status") or "active",
        "Source": arguments.get("source") or "manual",
        "SchemaVersion": int(arguments.get("schema_version") or 1),
        "Payload": arguments.get("payload"),
        "Tags": arguments.get("tags") or [],
    }
    return _http_json(
        "POST",
        "/api/integrations/health-mcp/insights",
        payload=payload,
        headers=_authorized_headers(access_token),
    )


def _tool_get_insights(arguments: dict[str, Any], headers: Any) -> dict[str, Any]:
    principal = _require_principal(headers)
    access_token, _account = _refresh_access_for_principal(principal)
    params: dict[str, Any] = {"limit": int(arguments.get("limit") or 200)}
    for key in ("insight_type", "period_type", "start_date", "end_date", "status", "source", "tag"):
        value = arguments.get(key)
        if value is not None and str(value).strip():
            params[key] = str(value).strip()
    path = f"/api/integrations/health-mcp/insights?{urllib.parse.urlencode(params)}"
    return _http_json("GET", path, headers=_authorized_headers(access_token))


def _tool_title(name: str) -> str:
    words = name.split("_")
    return " ".join(word.capitalize() for word in words)


def _tool_annotations(name: str) -> dict[str, Any]:
    read_only = name in READ_ONLY_TOOLS or name.startswith("get_") or name.startswith("search_")
    destructive = name in DESTRUCTIVE_TOOLS
    annotations = {
        "title": _tool_title(name),
        "readOnlyHint": read_only,
        "destructiveHint": destructive,
        "openWorldHint": False,
    }
    if read_only:
        annotations["idempotentHint"] = True
    elif name in IDEMPOTENT_WRITE_TOOLS:
        annotations["idempotentHint"] = True
    return annotations


TOOLS: dict[str, dict[str, Any]] = {
    "start_account_link": {
        "description": "Create a one-time browser link so the current MCP identity can sign into Everday without sending credentials through chat.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        "handler": _tool_start_account_link,
    },
    "connect_account": {
        "description": "Legacy direct-credential link flow. Prefer start_account_link so Everday credentials are entered on the hosted link page instead of in chat.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "username": {"type": "string"},
                "password": {"type": "string"},
            },
            "required": ["username", "password"],
            "additionalProperties": False,
        },
        "handler": _tool_connect_account,
    },
    "disconnect_account": {
        "description": "Disconnect the current remote MCP identity from its linked Everday account.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        "handler": _tool_disconnect_account,
    },
    "connection_status": {
        "description": "Show whether the current remote MCP identity is linked to an Everday account.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        "handler": _tool_connection_status,
    },
    "log_meal_text": {
        "description": "Log a meal from free text into the linked Everday user's health log.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "date": {"type": "string", "description": "YYYY-MM-DD. Defaults to today's server date."},
                "meal_type": {
                    "type": "string",
                    "enum": MEAL_SLOT_ENUM,
                    "description": "Everday meal slot. Use get_meal_slots for labels and aliases such as Bridge -> Snack2 and Dessert -> Snack3.",
                },
                "note": {"type": "string"},
            },
            "required": ["text"],
            "additionalProperties": False,
        },
        "handler": _tool_log_meal_text,
    },
    "log_meal_image": {
        "description": "Log a meal from a base64-encoded image into the linked Everday user's health log.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "image_base64": {"type": "string"},
                "date": {"type": "string", "description": "YYYY-MM-DD. Defaults to today's server date."},
                "meal_type": {
                    "type": "string",
                    "enum": MEAL_SLOT_ENUM,
                    "description": "Everday meal slot. Use get_meal_slots for labels and aliases such as Bridge -> Snack2 and Dessert -> Snack3.",
                },
                "note": {"type": "string"},
            },
            "required": ["image_base64"],
            "additionalProperties": False,
        },
        "handler": _tool_log_meal_image,
    },
    "log_meal_manual": {
        "description": "Log a meal with exact calories and optional macros into the linked Everday user's health log.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "food_name": {"type": "string"},
                "calories": {"type": "number", "minimum": 0},
                "protein": {"type": "number", "minimum": 0},
                "fibre": {"type": "number", "minimum": 0},
                "carbs": {"type": "number", "minimum": 0},
                "fat": {"type": "number", "minimum": 0},
                "saturated_fat": {"type": "number", "minimum": 0},
                "sugar": {"type": "number", "minimum": 0},
                "sodium": {"type": "number", "minimum": 0},
                "serving_quantity": {"type": "number", "exclusiveMinimum": 0},
                "serving_unit": {"type": "string"},
                "date": {"type": "string", "description": "YYYY-MM-DD. Defaults to today's server date."},
                "meal_type": {
                    "type": "string",
                    "enum": MEAL_SLOT_ENUM,
                    "description": "Everday meal slot. Use get_meal_slots for labels and aliases such as Bridge -> Snack2 and Dessert -> Snack3.",
                },
                "note": {"type": "string"},
            },
            "required": ["food_name", "calories"],
            "additionalProperties": False,
        },
        "handler": _tool_log_meal_manual,
    },
    "update_meal": {
        "description": "Update an existing meal entry by MealEntryId, including meal slot/date, quantity, notes, or corrected nutrition details.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "meal_entry_id": {"type": "string"},
                "date": {"type": "string", "description": "YYYY-MM-DD. Optional target date if moving the meal."},
                "meal_type": {
                    "type": "string",
                    "enum": MEAL_SLOT_ENUM,
                    "description": "Everday meal slot. Use get_meal_slots for labels and aliases such as Bridge -> Snack2 and Dessert -> Snack3.",
                },
                "note": {"type": "string"},
                "quantity": {"type": "number", "exclusiveMinimum": 0},
                "food_name": {"type": "string"},
                "calories": {"type": "number", "minimum": 0},
                "protein": {"type": "number", "minimum": 0},
                "fibre": {"type": "number", "minimum": 0},
                "carbs": {"type": "number", "minimum": 0},
                "fat": {"type": "number", "minimum": 0},
                "saturated_fat": {"type": "number", "minimum": 0},
                "sugar": {"type": "number", "minimum": 0},
                "sodium": {"type": "number", "minimum": 0},
                "serving_quantity": {"type": "number", "exclusiveMinimum": 0},
                "serving_unit": {"type": "string"},
            },
            "required": ["meal_entry_id"],
            "additionalProperties": False,
        },
        "handler": _tool_update_meal,
    },
    "delete_meal": {
        "description": "Delete an existing meal entry by MealEntryId.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "meal_entry_id": {"type": "string"},
            },
            "required": ["meal_entry_id"],
            "additionalProperties": False,
        },
        "handler": _tool_delete_meal,
    },
    "log_weight": {
        "description": "Log weight in kilograms into the linked Everday user's health log.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "weight_kg": {"type": "number"},
                "date": {"type": "string", "description": "YYYY-MM-DD. Defaults to today's server date."},
            },
            "required": ["weight_kg"],
            "additionalProperties": False,
        },
        "handler": _tool_log_weight,
    },
    "update_daily_log": {
        "description": "Update non-meal daily log fields such as water, sleep, work location, period, adherence, or notes for one date.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "YYYY-MM-DD. Defaults to today's server date."},
                "steps": {"type": "integer", "minimum": 0},
                "step_kcal_factor_override": {"type": "number", "minimum": 0},
                "weight_kg": {"type": "number", "minimum": 20, "maximum": 500},
                "office_mode": {
                    "type": "string",
                    "enum": ["office", "wfh", "other"],
                    "description": "Work location for the day. Use exactly one of: office, wfh, other.",
                },
                "water_litres": {"type": "number", "minimum": 0, "maximum": 20},
                "walking_pad_minutes": {"type": "integer", "minimum": 0, "maximum": 1440},
                "exercise_notes": {"type": "string"},
                "sleep_hours": {"type": "number", "minimum": 0, "maximum": 24},
                "period": {"type": "boolean", "description": "Set true if this is a period day, false otherwise."},
                "period_label": {
                    "type": "string",
                    "description": "Exact period status label, for example 'No', 'Day 1', or 'Day 2'. Prefer this when preserving workbook-style history.",
                },
                "hunger_before_dinner": {"type": "integer", "minimum": 1, "maximum": 10},
                "overall_satisfaction": {"type": "integer", "minimum": 1, "maximum": 10},
                "takeaway": {"type": "boolean", "description": "Set true if takeaway was eaten that day, false otherwise."},
                "logged_complete": {"type": "boolean", "description": "Set true when daily logging is complete, false otherwise."},
                "adherent_day": {"type": "boolean", "description": "Set true when the day counts as adherent, false otherwise."},
                "adherent_status": {
                    "type": "string",
                    "enum": ["yes", "no", "pending"],
                    "description": "Exact adherence status. Prefer this over adherent_day when preserving workbook-style history.",
                },
                "notes": {"type": "string"},
                "daily_calorie_target_snapshot": {"type": "integer", "minimum": 0},
                "protein_target_snapshot": {"type": "number", "minimum": 0},
                "step_target_snapshot": {"type": "integer", "minimum": 0},
            },
            "additionalProperties": False,
        },
        "handler": _tool_update_daily_log,
    },
    "log_workout": {
        "description": "Log a workout into the linked Everday user's health log.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "workout_type": {
                    "type": "string",
                    "enum": WORKOUT_TYPE_ENUM,
                    "description": "Workout type. Use get_workout_type_options for labels and aliases.",
                },
                "workout_name": {"type": "string"},
                "date": {"type": "string", "description": "YYYY-MM-DD. Defaults to today's server date."},
                "duration_minutes": {"type": "number"},
                "calories_burned": {"type": "integer", "minimum": 0},
                "distance_km": {"type": "number", "minimum": 0},
                "started_at": {"type": "string", "description": "ISO datetime, optional."},
                "ended_at": {"type": "string", "description": "ISO datetime, optional."},
                "notes": {"type": "string"},
            },
            "required": ["workout_type", "workout_name"],
            "additionalProperties": False,
        },
        "handler": _tool_log_workout,
    },
    "update_workout": {
        "description": "Update an existing workout by WorkoutId.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "workout_id": {"type": "string"},
                "date": {"type": "string", "description": "YYYY-MM-DD. Optional target date if moving the workout."},
                "workout_type": {
                    "type": "string",
                    "enum": WORKOUT_TYPE_ENUM,
                    "description": "Workout type. Use get_workout_type_options for labels and aliases.",
                },
                "workout_name": {"type": "string"},
                "duration_minutes": {"type": "number", "exclusiveMinimum": 0},
                "calories_burned": {"type": "integer", "minimum": 0},
                "distance_km": {"type": "number", "minimum": 0},
                "started_at": {"type": "string"},
                "ended_at": {"type": "string"},
                "notes": {"type": "string"},
            },
            "required": ["workout_id"],
            "additionalProperties": False,
        },
        "handler": _tool_update_workout,
    },
    "delete_workout": {
        "description": "Delete an existing workout by WorkoutId.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "workout_id": {"type": "string"},
            },
            "required": ["workout_id"],
            "additionalProperties": False,
        },
        "handler": _tool_delete_workout,
    },
    "get_today_summary": {
        "description": "Return the linked Everday user's daily calorie, meal, weight, and step summary.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "YYYY-MM-DD. Defaults to today's server date."},
            },
            "additionalProperties": False,
        },
        "handler": _tool_get_today_summary,
    },
    "get_goals": {
        "description": "Return the linked Everday user's current Everday targets, including dynamic calorie and step goals.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "YYYY-MM-DD. Defaults to today's server date."},
            },
            "additionalProperties": False,
        },
        "handler": _tool_get_goals,
    },
    "update_targets": {
        "description": "Update one or more current Everday health targets. These are account-level targets used for current and future daily summaries, not a one-day historical snapshot.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "daily_calorie_target": {"type": "integer", "minimum": 0},
                "protein_target_min": {"type": "number", "minimum": 0},
                "protein_target_max": {"type": "number", "minimum": 0},
                "step_target": {"type": "integer", "minimum": 0},
                "step_kcal_factor": {"type": "number", "minimum": 0},
                "fibre_target": {"type": "number", "minimum": 0},
                "carbs_target": {"type": "number", "minimum": 0},
                "fat_target": {"type": "number", "minimum": 0},
                "saturated_fat_target": {"type": "number", "minimum": 0},
                "sugar_target": {"type": "number", "minimum": 0},
                "sodium_target": {"type": "number", "minimum": 0},
            },
            "additionalProperties": False,
        },
        "handler": _tool_update_targets,
    },
    "get_connection_context": {
        "description": "Return the linked Everday user context, including timezone and meal-slot defaults, so the client can avoid date or enum guesses.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        "handler": _tool_get_connection_context,
    },
    "get_meal_type_options": {
        "description": "Return the valid meal_type values, labels, and aliases accepted by meal logging and meal update tools.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        "handler": _tool_get_meal_type_options,
    },
    "get_daily_log_fields": {
        "description": "Return the valid daily log fields, allowed values, and input constraints for update_daily_log.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        "handler": _tool_get_daily_log_fields,
    },
    "get_weight_trend": {
        "description": "Return recent weight trend data for the linked Everday user.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "minimum": 1, "maximum": 365},
            },
            "additionalProperties": False,
        },
        "handler": _tool_get_weight_trend,
    },
    "get_step_summary": {
        "description": "Return step totals, averages, and step calories for a date range.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "start_date": {"type": "string"},
                "end_date": {"type": "string"},
            },
            "required": ["start_date", "end_date"],
            "additionalProperties": False,
        },
        "handler": _tool_get_step_summary,
    },
    "get_targets_history": {
        "description": "Return the linked Everday user's current targets plus recent recommendation history snapshots.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
            "additionalProperties": False,
        },
        "handler": _tool_get_targets_history,
    },
    "get_meal": {
        "description": "Return one meal entry by MealEntryId with its current metadata.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "meal_entry_id": {"type": "string"},
            },
            "required": ["meal_entry_id"],
            "additionalProperties": False,
        },
        "handler": _tool_get_meal,
    },
    "get_today_meals": {
        "description": "Return just the linked Everday user's meals for one day, in a lighter shape than the full summary payload.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "YYYY-MM-DD. Defaults to today's server date."},
            },
            "additionalProperties": False,
        },
        "handler": _tool_get_today_meals,
    },
    "search_meals": {
        "description": "Search meal entries by text, optionally restricted to one date.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "date": {"type": "string", "description": "YYYY-MM-DD, optional."},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        "handler": _tool_search_meals,
    },
    "save_food_from_meal": {
        "description": "Promote a meal entry into a reusable saved food, marking the resulting food as a favourite.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "meal_entry_id": {"type": "string"},
                "food_name": {"type": "string"},
            },
            "required": ["meal_entry_id"],
            "additionalProperties": False,
        },
        "handler": _tool_save_food_from_meal,
    },
    "get_meal_slots": {
        "description": "Return the valid Everday meal slot values and their human labels so clients can update or log meals without guessing enum names.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        "handler": _tool_get_meal_slots,
    },
    "get_history_types": {
        "description": "Return the valid history_type values accepted by get_history.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        "handler": _tool_get_history_types,
    },
    "get_history_type_options": {
        "description": "Return the valid history_type values and their meanings accepted by get_history.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        "handler": _tool_get_history_type_options,
    },
    "get_workout_type_options": {
        "description": "Return the valid workout_type values, labels, and aliases accepted by workout logging and update tools.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        "handler": _tool_get_workout_type_options,
    },
    "get_history": {
        "description": "Return linked Everday history for weight, steps, workouts, day summaries, or meals.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "history_type": {
                    "type": "string",
                    "enum": HISTORY_TYPE_ENUM,
                    "description": "Use one of the documented history types. Call get_history_type_options if unsure.",
                },
                "start_date": {"type": "string"},
                "end_date": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500},
            },
            "required": ["history_type", "start_date", "end_date"],
            "additionalProperties": False,
        },
        "handler": _tool_get_history,
    },
    "search_saved_foods": {
        "description": "Search the linked Everday user's saved food definitions.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        "handler": _tool_search_saved_foods,
    },
    "get_saved_foods": {
        "description": "List the linked Everday user's saved food definitions.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "minimum": 1, "maximum": 500},
            },
            "additionalProperties": False,
        },
        "handler": _tool_get_saved_foods,
    },
    "upsert_recipe_review": {
        "description": "Create or update a structured recipe review for one recipe/date, including rating, would-make-again, Hall of Fame override, and notes.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "recipe_review_id": {"type": "string", "description": "Optional existing RecipeReviewId to update."},
                "recipe_name": {"type": "string"},
                "log_date": {"type": "string", "description": "YYYY-MM-DD."},
                "meal_entry_id": {"type": "string"},
                "rating": {"type": "number", "minimum": 0, "maximum": 10},
                "would_make_again": {"type": "string", "enum": ["yes", "no", "maybe"]},
                "hall_of_fame_override": {"type": "string", "enum": ["yes", "no"]},
                "notes": {"type": "string"},
            },
            "additionalProperties": False,
        },
        "handler": _tool_upsert_recipe_review,
    },
    "get_recipe_reviews": {
        "description": "List stored recipe reviews for the linked Everday user.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "minimum": 1, "maximum": 500},
            },
            "additionalProperties": False,
        },
        "handler": _tool_get_recipe_reviews,
    },
    "get_recipe_stats": {
        "description": "Return derived recipe statistics such as times eaten, average rating, average calories, average protein, and Hall of Fame state.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "YYYY-MM-DD, optional."},
                "end_date": {"type": "string", "description": "YYYY-MM-DD, optional."},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500},
            },
            "additionalProperties": False,
        },
        "handler": _tool_get_recipe_stats,
    },
    "upsert_product_review": {
        "description": "Create or update a structured product review including brand, category, buy-again decision, rating, and notes.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "food_id": {"type": "string"},
                "product_name": {"type": "string"},
                "brand": {"type": "string"},
                "category": {"type": "string"},
                "buy_again": {"type": "string", "enum": ["yes", "no", "maybe"]},
                "rating": {"type": "number", "minimum": 0, "maximum": 10},
                "calories_per_serving": {"type": "integer", "minimum": 0},
                "protein_per_serving": {"type": "number", "minimum": 0},
                "notes": {"type": "string"},
            },
            "required": ["product_name"],
            "additionalProperties": False,
        },
        "handler": _tool_upsert_product_review,
    },
    "get_product_reviews": {
        "description": "List stored product reviews for the linked Everday user.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "minimum": 1, "maximum": 500},
            },
            "additionalProperties": False,
        },
        "handler": _tool_get_product_reviews,
    },
    "upsert_experiment": {
        "description": "Create or update an experiment record with variable, expected outcome, actual outcome, decision, and status.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "experiment_id": {"type": "string"},
                "start_date": {"type": "string", "description": "YYYY-MM-DD."},
                "end_date": {"type": "string", "description": "YYYY-MM-DD, optional."},
                "variable_changed": {"type": "string"},
                "reason": {"type": "string"},
                "expected_outcome": {"type": "string"},
                "actual_outcome": {"type": "string"},
                "decision": {"type": "string", "enum": ["yes", "no", "maybe", "pending", "adopt", "reject", "inconclusive", "keep testing"]},
                "status": {"type": "string"},
            },
            "required": ["start_date", "variable_changed"],
            "additionalProperties": False,
        },
        "handler": _tool_upsert_experiment,
    },
    "get_experiments": {
        "description": "List stored experiments for the linked Everday user.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "minimum": 1, "maximum": 500},
            },
            "additionalProperties": False,
        },
        "handler": _tool_get_experiments,
    },
    "upsert_measurement": {
        "description": "Create or update a dated body measurement snapshot including waist, hips, resting heart rate, and cycle notes.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "log_date": {"type": "string", "description": "YYYY-MM-DD."},
                "waist_cm": {"type": "number", "minimum": 0, "maximum": 500},
                "hips_cm": {"type": "number", "minimum": 0, "maximum": 500},
                "resting_heart_rate": {"type": "integer", "minimum": 0, "maximum": 300},
                "period_cycle_notes": {"type": "string"},
                "notes": {"type": "string"},
            },
            "required": ["log_date"],
            "additionalProperties": False,
        },
        "handler": _tool_upsert_measurement,
    },
    "get_measurements": {
        "description": "List stored body measurements for the linked Everday user, optionally within a date range.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "YYYY-MM-DD, optional."},
                "end_date": {"type": "string", "description": "YYYY-MM-DD, optional."},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500},
            },
            "additionalProperties": False,
        },
        "handler": _tool_get_measurements,
    },
    "upsert_weekly_review_note": {
        "description": "Create or update the authored note fields for one weekly review period.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "week_start": {"type": "string", "description": "YYYY-MM-DD Monday date."},
                "biggest_nutrition_win": {"type": "string"},
                "improvement_for_next_week": {"type": "string"},
            },
            "required": ["week_start"],
            "additionalProperties": False,
        },
        "handler": _tool_upsert_weekly_review_note,
    },
    "get_weekly_review_note": {
        "description": "Return the stored authored note fields for one weekly review period.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "week_start": {"type": "string", "description": "YYYY-MM-DD Monday date."},
            },
            "required": ["week_start"],
            "additionalProperties": False,
        },
        "handler": _tool_get_weekly_review_note,
    },
    "get_weekly_review": {
        "description": "Return a derived weekly review snapshot with averages, adherence, best/worst meals, and attached authored weekly notes.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "week_start": {"type": "string", "description": "YYYY-MM-DD Monday date."},
            },
            "required": ["week_start"],
            "additionalProperties": False,
        },
        "handler": _tool_get_weekly_review,
    },
    "get_insight_type_options": {
        "description": "Return recommended insight_type values plus the valid period_type and status values for flexible insights.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        "handler": _tool_get_insight_type_options,
    },
    "upsert_insight": {
        "description": "Create or update a flexible insight record with a stable envelope and arbitrary JSON payload.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "insight_id": {"type": "string", "description": "Optional existing InsightId to update."},
                "insight_type": {
                    "type": "string",
                    "description": "Flexible insight category such as context_comparison, relationship_test, meal_pattern, weekly_pattern, or a future custom type. Use get_insight_type_options for recommended values.",
                },
                "period_type": {"type": "string", "enum": INSIGHT_PERIOD_ENUM},
                "period_start": {"type": "string", "description": "YYYY-MM-DD."},
                "period_end": {"type": "string", "description": "YYYY-MM-DD, optional."},
                "title": {"type": "string"},
                "summary": {"type": "string"},
                "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
                "status": {"type": "string", "enum": INSIGHT_STATUS_ENUM},
                "source": {"type": "string", "description": "For example workbook, derived, agent, or manual."},
                "schema_version": {"type": "integer", "minimum": 1, "maximum": 1000},
                "payload": {
                    "type": "object",
                    "description": "Flexible JSON object for workbook-specific fields, metrics, evidence, or narratives.",
                    "additionalProperties": True,
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional freeform tags for filtering later.",
                },
            },
            "required": ["insight_type", "period_type", "period_start", "title"],
            "additionalProperties": False,
        },
        "handler": _tool_upsert_insight,
    },
    "get_insights": {
        "description": "List stored insight records with optional filters by type, period, date range, status, source, or tag.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "insight_type": {"type": "string"},
                "period_type": {"type": "string", "enum": INSIGHT_PERIOD_ENUM},
                "start_date": {"type": "string", "description": "YYYY-MM-DD, optional."},
                "end_date": {"type": "string", "description": "YYYY-MM-DD, optional."},
                "status": {"type": "string", "enum": INSIGHT_STATUS_ENUM},
                "source": {"type": "string"},
                "tag": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500},
            },
            "additionalProperties": False,
        },
        "handler": _tool_get_insights,
    },
}


def _render_link_page(
    *,
    session: sqlite3.Row,
    error_message: str | None = None,
    linked_profile: dict[str, Any] | None = None,
) -> bytes:
    display_name = escape((session["external_name"] or session["external_email"] or session["external_subject"] or "this account"))
    expires_at = escape(session["expires_at"])
    status = session["status"]
    title = "Link Everday Account"
    body = ""
    if status == "completed":
        title = "Everday Account Linked"
        username = escape(session["everday_username"] or "")
        user_id = escape(str(session["everday_user_id"] or ""))
        profile_name = ""
        if linked_profile:
            parts = [str(linked_profile.get("FirstName") or "").strip(), str(linked_profile.get("LastName") or "").strip()]
            joined = " ".join(part for part in parts if part).strip()
            if joined:
                profile_name = f"<p><strong>Name:</strong> {escape(joined)}</p>"
        body = (
            f"<p>The Everday account is now linked for <strong>{display_name}</strong>.</p>"
            f"<p><strong>Everday username:</strong> {username}</p>"
            f"{profile_name}"
            f"<p><strong>Everday user ID:</strong> {user_id}</p>"
            "<p>You can close this tab and return to ChatGPT.</p>"
        )
    elif status != "pending":
        title = "Link Session Unavailable"
        reason = escape(session["last_error"] or "This account-link session is no longer active.")
        body = (
            f"<p>{reason}</p>"
            "<p>Return to ChatGPT and run <code>start_account_link</code> again to create a fresh link.</p>"
        )
    else:
        error_html = f"<div class='error'>{escape(error_message)}</div>" if error_message else ""
        body = (
            f"<p>This will link the Everday account for <strong>{display_name}</strong>.</p>"
            "<p>Your Everday credentials are submitted directly to this hosted page, not to ChatGPT.</p>"
            f"<p><strong>Link expires:</strong> {expires_at}</p>"
            f"{error_html}"
            "<form method='post'>"
            "<label for='username'>Everday username</label>"
            "<input id='username' name='username' type='text' autocomplete='username' required />"
            "<label for='password'>Everday password</label>"
            "<input id='password' name='password' type='password' autocomplete='current-password' required />"
            "<button type='submit'>Link Everday Account</button>"
            "</form>"
        )
    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{title}</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f4f6f8;
      --panel: #ffffff;
      --text: #15202b;
      --muted: #52606d;
      --accent: #0f766e;
      --border: #d9e2ec;
      --danger-bg: #fff1f2;
      --danger-border: #fecdd3;
      --danger-text: #9f1239;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: linear-gradient(180deg, #eef7f6 0%, var(--bg) 100%);
      color: var(--text);
      min-height: 100vh;
      display: grid;
      place-items: center;
      padding: 24px;
    }}
    .card {{
      width: min(100%, 460px);
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 16px;
      box-shadow: 0 20px 50px rgba(15, 23, 42, 0.12);
      padding: 28px;
    }}
    h1 {{
      margin: 0 0 12px;
      font-size: 1.6rem;
    }}
    p {{
      margin: 0 0 14px;
      color: var(--muted);
      line-height: 1.45;
    }}
    strong, code {{
      color: var(--text);
    }}
    form {{
      display: grid;
      gap: 12px;
      margin-top: 18px;
    }}
    label {{
      font-size: 0.95rem;
      font-weight: 600;
      color: var(--text);
    }}
    input {{
      width: 100%;
      padding: 12px 14px;
      border: 1px solid var(--border);
      border-radius: 10px;
      font: inherit;
    }}
    button {{
      margin-top: 6px;
      border: 0;
      border-radius: 10px;
      padding: 12px 16px;
      font: inherit;
      font-weight: 700;
      color: #ffffff;
      background: var(--accent);
      cursor: pointer;
    }}
    .error {{
      margin: 16px 0 0;
      padding: 12px 14px;
      border-radius: 10px;
      border: 1px solid var(--danger-border);
      background: var(--danger-bg);
      color: var(--danger-text);
      font-weight: 600;
    }}
  </style>
</head>
<body>
  <main class="card">
    <h1>{title}</h1>
    {body}
  </main>
</body>
</html>
"""
    return html.encode("utf-8")


def _handle_jsonrpc(payload: dict[str, Any], headers: Any) -> dict[str, Any] | None:
    request_id = payload.get("id")
    method = payload.get("method")
    params = payload.get("params") or {}

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": "2025-06-18",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "health-mcp", "version": Config.version},
            },
        }

    if method == "notifications/initialized":
        if request_id is None:
            return None
        return {"jsonrpc": "2.0", "id": request_id, "result": {}}

    if method == "ping":
        return {"jsonrpc": "2.0", "id": request_id, "result": {}}

    if method == "tools/list":
        tools = []
        for name, spec in TOOLS.items():
            tools.append(
                {
                    "name": name,
                    "description": spec["description"],
                    "inputSchema": spec["inputSchema"],
                    "annotations": spec.get("annotations") or _tool_annotations(name),
                }
            )
        return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": tools}}

    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if name not in TOOLS:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": _json_response({"error": f"Unknown tool: {name}"}, tool_error=True),
            }
        handler = TOOLS[name]["handler"]
        try:
            result = handler(arguments, headers)
            if isinstance(result, dict):
                awareness = _task_awareness(headers)
                if awareness is not None:
                    result["TaskAwareness"] = awareness
                    if awareness["AgentNotice"]:
                        result["AgentNotice"] = awareness["AgentNotice"]
            return {"jsonrpc": "2.0", "id": request_id, "result": _json_response(result)}
        except (ValueError, RuntimeError) as exc:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": _json_response({"error": str(exc)}, tool_error=True),
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": _json_response({"error": f"Unhandled tool failure: {exc}"}, tool_error=True),
            }

    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[health-mcp] {self.client_address[0]} - " + fmt % args, flush=True)

    def _send_bytes(
        self,
        status: int,
        body: bytes,
        content_type: str,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)
        self.close_connection = True

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        self._send_bytes(
            status,
            json.dumps(payload).encode("utf-8"),
            "application/json; charset=utf-8",
        )

    def _send_html(self, status: int, body: bytes) -> None:
        self._send_bytes(status, body, "text/html; charset=utf-8")

    def _render_link_error(self, status: int, message: str) -> None:
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
        self._send_html(status, _render_link_page(session=fake_session))  # type: ignore[arg-type]

    def _handle_link_get(self, session_token: str) -> None:
        session = _load_link_session(session_token)
        if session is None:
            self._render_link_error(404, "This account-link session does not exist.")
            return
        if session["status"] == "pending":
            try:
                session = _active_link_session_or_error(session_token)
            except ValueError as exc:
                session = _load_link_session(session_token) or session
                self._send_html(410, _render_link_page(session=session, error_message=str(exc)))
                return
        linked_profile = None
        if session["status"] == "completed":
            account = _load_account(session["external_subject"])
            if account is not None:
                try:
                    access_token, _ = _refresh_access_for_principal(
                        {
                            "subject": session["external_subject"],
                            "email": session["external_email"],
                            "name": session["external_name"],
                        }
                    )
                    linked_profile = _everday_profile(access_token)
                except Exception:
                    linked_profile = None
        self._send_html(200, _render_link_page(session=session, linked_profile=linked_profile))

    def _handle_link_post(self, session_token: str) -> None:
        try:
            session = _active_link_session_or_error(session_token)
        except ValueError as exc:
            existing = _load_link_session(session_token)
            if existing is None:
                self._render_link_error(404, str(exc))
                return
            self._send_html(410, _render_link_page(session=existing, error_message=str(exc)))
            return

        try:
            content_length = int(self.headers.get("Content-Length", "0") or "0")
        except (TypeError, ValueError):
            content_length = 0
        if content_length <= 0 or content_length > 16384:
            self._send_html(400, _render_link_page(session=session, error_message="The submitted form was invalid."))
            return

        raw = self.rfile.read(content_length)
        form = urllib.parse.parse_qs(raw.decode("utf-8", errors="replace"), keep_blank_values=False)
        username = (form.get("username") or [""])[0].strip()
        password = (form.get("password") or [""])[0]
        if not username or not password:
            self._send_html(
                400,
                _render_link_page(session=session, error_message="Everday username and password are required."),
            )
            return

        try:
            login = _everday_login(username, password)
            access_token = str(login.get("AccessToken") or "").strip()
            refresh_token = str(login.get("RefreshToken") or "").strip()
            if not access_token or not refresh_token:
                raise RuntimeError("Everday login did not return usable tokens.")
            profile = _everday_profile(access_token)
            everday_user_id = int(profile.get("UserId"))
            _save_account(
                session["external_subject"],
                session["external_email"],
                session["external_name"],
                everday_user_id,
                username,
                refresh_token,
            )
            _mark_link_session(
                session_token,
                status="completed",
                everday_user_id=everday_user_id,
                everday_username=username,
                completed_at=_utc_now().isoformat(),
                last_error=None,
            )
            updated = _load_link_session(session_token) or session
            self._send_html(200, _render_link_page(session=updated, linked_profile=profile))
        except (ValueError, RuntimeError) as exc:
            _mark_link_session(
                session_token,
                status="pending",
                everday_user_id=session["everday_user_id"],
                everday_username=session["everday_username"],
                completed_at=session["completed_at"],
                last_error=str(exc),
            )
            refreshed = _load_link_session(session_token) or session
            self._send_html(400, _render_link_page(session=refreshed, error_message=str(exc)))

    def do_GET(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        if path == "/healthz":
            self._send_bytes(200, b"ok\n", "text/plain; charset=utf-8")
            return
        if path == "/version":
            self._send_json(
                200,
                {
                    "service": "health-mcp",
                    "version": Config.version,
                    "everday_base_url": Config.everday_base_url,
                    "tools": sorted(TOOLS.keys()),
                },
            )
            return
        if path == "/":
            self._send_json(200, {"service": "health-mcp", "version": Config.version})
            return
        if path.startswith("/link/"):
            session_token = path.removeprefix("/link/").strip("/")
            if not session_token:
                self.send_error(404, "Not Found")
                return
            self._handle_link_get(session_token)
            return
        if path == "/mcp":
            self.send_error(405, "Use POST for MCP requests")
            return
        self.send_error(404, "Not Found")

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_OPTIONS(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        if path == "/mcp":
            self.send_response(204)
            self.send_header("Allow", "POST, OPTIONS")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_error(404, "Not Found")

    def do_POST(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        if path.startswith("/link/"):
            session_token = path.removeprefix("/link/").strip("/")
            if not session_token:
                self.send_error(404, "Not Found")
                return
            self._handle_link_post(session_token)
            return
        if path != "/mcp":
            self.send_error(404, "Not Found")
            return

        try:
            content_length = int(self.headers.get("Content-Length", "0") or "0")
        except (TypeError, ValueError):
            content_length = 0
        if content_length <= 0:
            self._send_json(400, {"error": "empty_request"})
            return
        if content_length > Config.max_request_bytes:
            self._send_json(413, {"error": "request_too_large"})
            return

        raw = self.rfile.read(content_length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            self._send_json(400, {"error": "invalid_json"})
            return
        if not isinstance(payload, dict):
            self._send_json(400, {"error": "invalid_request"})
            return

        response = _handle_jsonrpc(payload, self.headers)
        if response is None:
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self._send_json(200, response)


def main() -> None:
    _init_db()
    server = ThreadingHTTPServer((Config.host, Config.port), Handler)
    print(f"[health-mcp] listening on http://{Config.host}:{Config.port}", flush=True)
    with server:
        server.serve_forever(poll_interval=0.5)


if __name__ == "__main__":
    main()
