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

from cryptography.fernet import Fernet, InvalidToken


def _require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


class Config:
    host = os.environ.get("HEALTH_MCP_HOST", "0.0.0.0").strip() or "0.0.0.0"
    port = int(os.environ.get("HEALTH_MCP_PORT", "8766"))
    version = os.environ.get("HEALTH_MCP_VERSION", "2026-07-16.1").strip() or "2026-07-16.1"
    provider = os.environ.get("HEALTH_MCP_PROVIDER", "authelia").strip() or "authelia"
    public_base_url = os.environ.get("HEALTH_MCP_PUBLIC_BASE_URL", "").strip().rstrip("/")
    everday_base_url = _require_env("HEALTH_MCP_EVERDAY_BASE_URL").rstrip("/")
    state_db_path = os.environ.get("HEALTH_MCP_STATE_DB_PATH", "/data/health_mcp.sqlite3").strip() or "/data/health_mcp.sqlite3"
    encryption_key = _require_env("HEALTH_MCP_ENCRYPTION_KEY")
    timeout_seconds = max(float(os.environ.get("HEALTH_MCP_TIMEOUT_SECONDS", "30")), 1.0)
    max_request_bytes = max(int(os.environ.get("HEALTH_MCP_MAX_REQUEST_BYTES", "10485760")), 1)
    link_session_ttl_minutes = max(int(os.environ.get("HEALTH_MCP_LINK_SESSION_TTL_MINUTES", "30")), 1)


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
        conn.commit()


def _db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(Config.state_db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


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
        "hunger_before_dinner": "HungerBeforeDinner",
        "overall_satisfaction": "OverallSatisfaction",
        "takeaway": "Takeaway",
        "logged_complete": "LoggedComplete",
        "adherent_day": "AdherentDay",
        "notes": "Notes",
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
    return {
        "MealSlots": [
            {
                "value": "Breakfast",
                "label": "Breakfast",
                "aliases": ["breakfast"],
                "order": 1,
            },
            {
                "value": "Snack1",
                "label": "Morning snack",
                "aliases": ["morning snack", "snack1", "snack 1"],
                "order": 2,
            },
            {
                "value": "Lunch",
                "label": "Lunch",
                "aliases": ["lunch"],
                "order": 3,
            },
            {
                "value": "Snack2",
                "label": "Afternoon snack",
                "aliases": ["afternoon snack", "bridge", "snack2", "snack 2"],
                "order": 4,
            },
            {
                "value": "Dinner",
                "label": "Dinner",
                "aliases": ["dinner"],
                "order": 5,
            },
            {
                "value": "Snack3",
                "label": "Evening snack",
                "aliases": ["evening snack", "dessert", "night snack", "snack3", "snack 3"],
                "order": 6,
            },
        ]
    }


def _tool_get_history_types(arguments: dict[str, Any], headers: Any) -> dict[str, Any]:
    _require_principal(headers)
    return {
        "HistoryTypes": [
            {"value": "weight", "description": "Per-day weight history."},
            {"value": "steps", "description": "Per-day step history and step calories."},
            {"value": "workouts", "description": "Workout ledger entries."},
            {"value": "days", "description": "Per-day calorie and burn summaries."},
            {"value": "meals", "description": "Meal entry history with ids and nutrition metadata."},
        ]
    }


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
                "meal_type": {"type": "string"},
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
                "meal_type": {"type": "string"},
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
                "meal_type": {"type": "string"},
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
                "meal_type": {"type": "string"},
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
                "office_mode": {"type": "string"},
                "water_litres": {"type": "number", "minimum": 0, "maximum": 20},
                "walking_pad_minutes": {"type": "integer", "minimum": 0, "maximum": 1440},
                "exercise_notes": {"type": "string"},
                "sleep_hours": {"type": "number", "minimum": 0, "maximum": 24},
                "period": {"type": "boolean"},
                "hunger_before_dinner": {"type": "integer", "minimum": 1, "maximum": 10},
                "overall_satisfaction": {"type": "integer", "minimum": 1, "maximum": 10},
                "takeaway": {"type": "boolean"},
                "logged_complete": {"type": "boolean"},
                "adherent_day": {"type": "boolean"},
                "notes": {"type": "string"},
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
                "workout_type": {"type": "string"},
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
                "workout_type": {"type": "string"},
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
    "get_connection_context": {
        "description": "Return the linked Everday user context, including timezone and meal-slot defaults, so the client can avoid date or enum guesses.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        "handler": _tool_get_connection_context,
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
    "get_history": {
        "description": "Return linked Everday history for weight, steps, workouts, day summaries, or meals.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "history_type": {"type": "string", "enum": ["weight", "steps", "workouts", "days", "meals"]},
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
