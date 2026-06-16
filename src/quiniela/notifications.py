from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
import sqlite3
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

import requests

from quiniela.config import Settings, get_settings
from quiniela.db import get_connection
from quiniela.name_maps import normalize_team_name, normalize_text


NOTIFICATION_WINDOWS = (15, 5)
RETRY_DELAYS_MINUTES = (1, 2, 4, 5)
OPEN_STATUSES = ("pending", "waiting_prediction", "retry", "sending")
TERMINAL_MATCH_STATUSES = {"FT", "AET", "PEN", "PST", "CANC", "ABD", "AWD", "WO"}
PREDICTION_WINDOW_NOTIFICATION = "prediction_window"
OFFICIAL_LINEUP_NOTIFICATION = "official_lineup"


@dataclass(frozen=True)
class DeliveryResult:
    success: bool
    status_code: int | None = None
    retryable: bool = False
    retry_after_seconds: int | None = None
    error_code: str | None = None


def _as_datetime(value: str, timezone_name: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(timezone_name))
    return parsed


def _utc_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _retry_after_seconds(value: str | None, now: datetime) -> int | None:
    if not value:
        return None
    try:
        return max(0, int(float(value)))
    except ValueError:
        pass
    try:
        retry_at = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=timezone.utc)
    return max(0, int((retry_at - now.astimezone(timezone.utc)).total_seconds()))


def _response_result(response: requests.Response, now: datetime) -> DeliveryResult:
    status_code = int(response.status_code)
    if 200 <= status_code < 300:
        return DeliveryResult(success=True, status_code=status_code)
    retryable = status_code in {408, 429} or status_code >= 500
    return DeliveryResult(
        success=False,
        status_code=status_code,
        retryable=retryable,
        retry_after_seconds=_retry_after_seconds(
            response.headers.get("Retry-After"),
            now,
        ),
        error_code=f"http_{status_code}",
    )


class NtfyAdapter:
    channel = "ntfy"

    def __init__(
        self,
        settings: Settings,
        session: requests.Session | None = None,
    ) -> None:
        self.settings = settings
        self.session = session or requests.Session()

    def send(self, payload: dict[str, Any], now: datetime) -> DeliveryResult:
        topic = quote(self.settings.ntfy_topic, safe="")
        url = f"{self.settings.ntfy_server_url}/{topic}"
        headers = {
            "Title": str(payload["title"]),
            "Priority": str(payload.get("priority") or ("5" if payload["window_label"] == "t-5" else "4")),
            "Tags": "soccer",
        }
        try:
            response = self.session.post(
                url,
                data=str(payload["message"]).encode("utf-8"),
                headers=headers,
                timeout=self.settings.notification_timeout_seconds,
            )
        except requests.RequestException:
            return DeliveryResult(
                success=False,
                retryable=True,
                error_code="network_error",
            )
        return _response_result(response, now)


class DiscordAdapter:
    channel = "discord"

    def __init__(
        self,
        settings: Settings,
        session: requests.Session | None = None,
    ) -> None:
        self.settings = settings
        self.session = session or requests.Session()

    def send(self, payload: dict[str, Any], now: datetime) -> DeliveryResult:
        fields = payload.get("fields") or [
            {"name": "Poisson", "value": payload["poisson"], "inline": True},
            {"name": "Hibrido", "value": payload["hybrid"], "inline": True},
            {"name": "1-X-2", "value": payload["outcomes"], "inline": False},
            {"name": "Alineaciones", "value": payload["lineups"], "inline": False},
            {"name": "Frescura", "value": payload["freshness"], "inline": False},
            {"name": "Modelos", "value": payload["models"], "inline": False},
        ]
        body = {
            "allowed_mentions": {"parse": []},
            "embeds": [
                {
                    "title": payload["title"],
                    "description": payload["kickoff"],
                    "color": int(
                        payload.get("color")
                        or (0xD97706 if payload["window_label"] == "t-5" else 0x2563EB)
                    ),
                    "fields": fields,
                }
            ],
        }
        try:
            response = self.session.post(
                self.settings.discord_webhook_url,
                json=body,
                timeout=self.settings.notification_timeout_seconds,
            )
        except requests.RequestException:
            return DeliveryResult(
                success=False,
                retryable=True,
                error_code="network_error",
            )
        return _response_result(response, now)


def _configured_channels(settings: Settings) -> tuple[list[str], list[str]]:
    channels: list[str] = []
    errors: list[str] = []
    if settings.ntfy_enabled:
        if settings.ntfy_server_url and settings.ntfy_topic:
            channels.append("ntfy")
        else:
            errors.append("ntfy_missing_configuration")
    if settings.discord_enabled:
        if settings.discord_webhook_url:
            channels.append("discord")
        else:
            errors.append("discord_missing_configuration")
    return channels, errors


def _window_minutes(window_label: str) -> int:
    return int(window_label.removeprefix("t-"))


def _starting_xi_names(payload: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for player_row in payload.get("startXI") or []:
        name = str(player_row.get("player", {}).get("name") or "").strip()
        if name:
            names.append(name)
    return names


def official_lineup_hash(payload: dict[str, Any]) -> str | None:
    normalized_names = [
        normalize_text(name)
        for name in _starting_xi_names(payload)
        if normalize_text(name)
    ]
    if len(normalized_names) < 11:
        return None
    return hashlib.sha256("|".join(normalized_names).encode("utf-8")).hexdigest()


def queue_official_lineup_notifications(
    connection: sqlite3.Connection,
    *,
    match_id: str,
    kickoff_at: str,
    lineups_payload: dict[str, Any],
    now: datetime | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    if not settings.notifications_enabled:
        return {"enabled": False, "scheduled": 0, "superseded": 0, "errors": []}

    channels, errors = _configured_channels(settings)
    if not channels:
        return {"enabled": True, "scheduled": 0, "superseded": 0, "errors": errors}

    local_tz = ZoneInfo(settings.local_timezone)
    local_now = now.astimezone(local_tz) if now else datetime.now(local_tz)
    now_iso = _utc_iso(local_now)
    scheduled = 0
    superseded = 0

    for lineup in lineups_payload.get("response", []):
        team_name = str(lineup.get("team", {}).get("name") or "").strip()
        team_norm = normalize_team_name(team_name)
        lineup_hash = official_lineup_hash(lineup)
        if not team_norm or lineup_hash is None:
            continue
        window_label = f"official:{team_norm}:{lineup_hash[:12]}"
        for channel in channels:
            with connection:
                superseded += connection.execute(
                    """
                    UPDATE notification_deliveries
                    SET status = 'superseded',
                        error_code = 'official_lineup_updated',
                        updated_at = ?
                    WHERE notification_type = ?
                      AND match_id = ?
                      AND channel = ?
                      AND team_norm = ?
                      AND status IN ('pending', 'waiting_prediction', 'retry', 'sending')
                      AND lineup_hash <> ?
                    """,
                    (
                        now_iso,
                        OFFICIAL_LINEUP_NOTIFICATION,
                        match_id,
                        channel,
                        team_norm,
                        lineup_hash,
                    ),
                ).rowcount
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO notification_deliveries (
                        match_id, kickoff_at, window_label, channel,
                        notification_type, team_norm, lineup_hash, scheduled_for,
                        status, next_attempt_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
                    """,
                    (
                        match_id,
                        kickoff_at,
                        window_label,
                        channel,
                        OFFICIAL_LINEUP_NOTIFICATION,
                        team_norm,
                        lineup_hash,
                        now_iso,
                        now_iso,
                    ),
                )
            scheduled += int(cursor.rowcount > 0)

    return {
        "enabled": True,
        "scheduled": scheduled,
        "superseded": superseded,
        "errors": errors,
    }


def schedule_due_notifications(
    connection: sqlite3.Connection,
    now: datetime | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    if not settings.notifications_enabled:
        return {"enabled": False, "scheduled": 0, "superseded": 0, "errors": []}

    channels, errors = _configured_channels(settings)
    if not channels:
        return {"enabled": True, "scheduled": 0, "superseded": 0, "errors": errors}

    local_tz = ZoneInfo(settings.local_timezone)
    local_now = now.astimezone(local_tz) if now else datetime.now(local_tz)
    now_iso = _utc_iso(local_now)
    matches = connection.execute(
        """
        SELECT match_id, datetime_cdmx, status
        FROM matches
        WHERE julianday(datetime_cdmx) > julianday(?)
        ORDER BY julianday(datetime_cdmx)
        """,
        (now_iso,),
    ).fetchall()
    scheduled = 0
    superseded = 0

    for match in matches:
        if str(match["status"] or "").upper() in TERMINAL_MATCH_STATUSES:
            continue
        kickoff = _as_datetime(str(match["datetime_cdmx"]), settings.local_timezone)
        due_windows = [
            minutes
            for minutes in NOTIFICATION_WINDOWS
            if kickoff - timedelta(minutes=minutes) <= local_now < kickoff
        ]
        if not due_windows:
            continue
        desired_minutes = min(due_windows)
        window_label = f"t-{desired_minutes}"
        kickoff_at = kickoff.isoformat()
        scheduled_for = (kickoff - timedelta(minutes=desired_minutes)).isoformat()

        with connection:
            connection.execute(
                """
                UPDATE notification_deliveries
                SET status = 'expired',
                    error_code = 'kickoff_changed',
                    updated_at = ?
                WHERE match_id = ?
                  AND kickoff_at <> ?
                  AND status IN ('pending', 'waiting_prediction', 'retry', 'sending')
                """,
                (now_iso, str(match["match_id"]), kickoff_at),
            )

        open_rows = connection.execute(
            """
            SELECT id, window_label
            FROM notification_deliveries
            WHERE match_id = ?
              AND kickoff_at = ?
              AND notification_type = ?
              AND status IN ('pending', 'waiting_prediction', 'retry', 'sending')
            """,
            (str(match["match_id"]), kickoff_at, PREDICTION_WINDOW_NOTIFICATION),
        ).fetchall()
        older_ids = [
            int(row["id"])
            for row in open_rows
            if _window_minutes(str(row["window_label"])) > desired_minutes
        ]
        if older_ids:
            placeholders = ",".join("?" for _ in older_ids)
            with connection:
                connection.execute(
                    f"""
                    UPDATE notification_deliveries
                    SET status = 'superseded',
                        error_code = 'newer_window_due',
                        updated_at = ?
                    WHERE id IN ({placeholders})
                    """,
                    (now_iso, *older_ids),
                )
            superseded += len(older_ids)

        for channel in channels:
            with connection:
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO notification_deliveries (
                        match_id, kickoff_at, window_label, channel,
                        notification_type, scheduled_for, status, next_attempt_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)
                    """,
                    (
                        str(match["match_id"]),
                        kickoff_at,
                        window_label,
                        channel,
                        PREDICTION_WINDOW_NOTIFICATION,
                        scheduled_for,
                        now_iso,
                    ),
                )
            scheduled += int(cursor.rowcount > 0)

    return {
        "enabled": True,
        "scheduled": scheduled,
        "superseded": superseded,
        "errors": errors,
    }


def _lineup_label(value: str | None) -> str:
    labels = {
        "confirmed_lineup": "oficial",
        "official": "oficial",
        "web_estimated": "estimada por noticias",
        "recent_lineup": "ultimo once conocido",
        "season_stats": "estimada por temporada",
    }
    if not value:
        return "no disponible"
    return labels.get(value, value.replace("_", " "))


def _percentage(value: Any) -> str:
    if value is None:
        return "n/d"
    return f"{float(value):.1%}"


def _latest_prediction_row(
    connection: sqlite3.Connection,
    match_id: str,
    now: datetime,
) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT
            p.id AS prediction_id,
            p.predicted_score,
            p.probability,
            p.hybrid_predicted_score,
            p.hybrid_probability,
            p.home_win_probability,
            p.draw_probability,
            p.away_win_probability,
            p.model_version,
            p.outcome_model_version,
            p.data_freshness_at,
            p.source_json,
            m.home_team,
            m.away_team,
            m.datetime_cdmx,
            m.time_cdmx
        FROM predictions p
        INNER JOIN matches m ON m.match_id = p.match_id
        WHERE p.match_id = ?
          AND COALESCE(p.is_pre_kickoff, 1) = 1
          AND julianday(COALESCE(p.generated_at_utc, p.generated_at))
              < julianday(m.datetime_cdmx)
          AND julianday(COALESCE(p.generated_at_utc, p.generated_at))
              <= julianday(?)
        ORDER BY julianday(COALESCE(p.generated_at_utc, p.generated_at)) DESC,
                 p.id DESC
        LIMIT 1
        """,
        (match_id, _utc_iso(now)),
    ).fetchone()


def _prediction_summary_from_row(row: sqlite3.Row) -> dict[str, str]:
    source = json.loads(row["source_json"] or "{}")
    poisson = (
        f"{row['predicted_score']} "
        f"({_percentage(row['probability'])})"
    )
    hybrid = (
        f"{row['hybrid_predicted_score']} "
        f"({_percentage(row['hybrid_probability'])})"
        if row["hybrid_predicted_score"]
        else "no disponible"
    )
    outcomes = (
        f"1 {_percentage(row['home_win_probability'])} | "
        f"X {_percentage(row['draw_probability'])} | "
        f"2 {_percentage(row['away_win_probability'])}"
    )
    lineups = (
        f"{row['home_team']}: {_lineup_label(source.get('home_lineup_source'))}; "
        f"{row['away_team']}: {_lineup_label(source.get('away_lineup_source'))}"
    )
    freshness = str(row["data_freshness_at"] or "no disponible")
    models = (
        f"{row['model_version']} / "
        f"{row['outcome_model_version'] or 'Poisson fallback'}"
    )
    kickoff = f"Inicio: {row['time_cdmx']} (America/Mexico_City)"
    return {
        "poisson": poisson,
        "hybrid": hybrid,
        "outcomes": outcomes,
        "lineups": lineups,
        "freshness": freshness,
        "models": models,
        "kickoff": kickoff,
    }


def _latest_prediction_payload(
    connection: sqlite3.Connection,
    match_id: str,
    now: datetime,
    window_label: str,
) -> tuple[int, dict[str, Any]] | None:
    row = _latest_prediction_row(connection, match_id, now)
    if row is None:
        return None

    summary = _prediction_summary_from_row(row)
    title = (
        f"Pronostico {window_label.upper()}: "
        f"{row['home_team']} vs {row['away_team']}"
    )
    message = "\n".join(
        [
            summary["kickoff"],
            f"Poisson: {summary['poisson']}",
            f"Hibrido: {summary['hybrid']}",
            f"1-X-2: {summary['outcomes']}",
            f"Alineaciones: {summary['lineups']}",
            f"Frescura: {summary['freshness']}",
            f"Modelos: {summary['models']}",
        ]
    )
    payload = {
        "title": title,
        "message": message,
        "window_label": window_label,
        "notification_type": PREDICTION_WINDOW_NOTIFICATION,
        **summary,
    }
    return int(row["prediction_id"]), payload


def _official_lineup_payload(
    connection: sqlite3.Connection,
    match_id: str,
    team_norm: str,
    lineup_hash: str,
    now: datetime,
    window_label: str,
) -> dict[str, Any]:
    match = connection.execute(
        """
        SELECT
            m.match_id,
            m.home_team,
            m.away_team,
            m.home_team_norm,
            m.away_team_norm,
            m.api_fixture_id,
            m.time_cdmx
        FROM matches m
        WHERE m.match_id = ?
        """,
        (match_id,),
    ).fetchone()
    if match is None or match["api_fixture_id"] is None:
        return {"status": "lineup_missing"}

    fixture_id = str(int(match["api_fixture_id"]))
    lineup_row = connection.execute(
        """
        SELECT source_json
        FROM historical_lineups
        WHERE fixture_id = ? AND team_norm = ?
        """,
        (fixture_id, team_norm),
    ).fetchone()
    if lineup_row is None:
        return {"status": "lineup_missing"}

    lineup_payload = json.loads(lineup_row["source_json"] or "{}")
    current_hash = official_lineup_hash(lineup_payload)
    if current_hash is None:
        return {"status": "lineup_missing"}
    if current_hash != lineup_hash:
        return {"status": "lineup_changed"}

    prediction_row = _latest_prediction_row(connection, match_id, now)
    if prediction_row is None:
        return {"status": "prediction_missing"}

    if team_norm == str(match["home_team_norm"]):
        team_name = str(match["home_team"])
        opponent = str(match["away_team"])
    else:
        team_name = str(match["away_team"])
        opponent = str(match["home_team"])
    starters = _starting_xi_names(lineup_payload)
    summary = _prediction_summary_from_row(prediction_row)
    xi_text = ", ".join(starters)
    message = "\n".join(
        [
            summary["kickoff"],
            f"Rival: {opponent}",
            f"XI oficial {team_name}: {xi_text}",
            f"Mini pronostico: Poisson {summary['poisson']}",
            f"Hibrido: {summary['hybrid']}",
            f"1-X-2: {summary['outcomes']}",
        ]
    )
    payload = {
        "title": f"Alineacion oficial: {team_name}",
        "message": message,
        "window_label": window_label,
        "notification_type": OFFICIAL_LINEUP_NOTIFICATION,
        "kickoff": f"{summary['kickoff']}\nVs {opponent}",
        "poisson": summary["poisson"],
        "hybrid": summary["hybrid"],
        "outcomes": summary["outcomes"],
        "lineups": summary["lineups"],
        "freshness": summary["freshness"],
        "models": summary["models"],
        "priority": "4",
        "color": 0x16A34A,
        "fields": [
            {"name": "Rival", "value": opponent, "inline": True},
            {"name": "Poisson", "value": summary["poisson"], "inline": True},
            {"name": "Hibrido", "value": summary["hybrid"], "inline": True},
            {"name": "1-X-2", "value": summary["outcomes"], "inline": False},
            {"name": f"XI oficial {team_name}", "value": xi_text, "inline": False},
        ],
    }
    return {
        "status": "ready",
        "prediction_id": int(prediction_row["prediction_id"]),
        "payload": payload,
    }


def _adapter(
    channel: str,
    settings: Settings,
    session: requests.Session | None,
) -> NtfyAdapter | DiscordAdapter:
    if channel == "ntfy":
        return NtfyAdapter(settings, session=session)
    if channel == "discord":
        return DiscordAdapter(settings, session=session)
    raise ValueError(f"Canal de notificacion desconocido: {channel}")


def dispatch_notifications(
    connection: sqlite3.Connection | None = None,
    now: datetime | None = None,
    settings: Settings | None = None,
    sessions: dict[str, requests.Session] | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    connection = connection or get_connection(settings.db_path)
    if not settings.notifications_enabled:
        return {"enabled": False, "sent": 0, "retry": 0, "failed": 0, "expired": 0}

    configured_channels, configuration_errors = _configured_channels(settings)
    configured_channel_set = set(configured_channels)
    local_tz = ZoneInfo(settings.local_timezone)
    local_now = now.astimezone(local_tz) if now else datetime.now(local_tz)
    now_iso = _utc_iso(local_now)
    stale_sending = _utc_iso(local_now - timedelta(minutes=10))
    with connection:
        connection.execute(
            """
            UPDATE notification_deliveries
            SET status = 'retry',
                error_code = 'stale_delivery_recovered',
                next_attempt_at = ?,
                updated_at = ?
            WHERE status = 'sending'
              AND julianday(updated_at) <= julianday(?)
            """,
            (now_iso, now_iso, stale_sending),
        )
        expired_cursor = connection.execute(
            """
            UPDATE notification_deliveries
            SET status = 'expired',
                error_code = 'kickoff_reached',
                updated_at = ?
            WHERE status IN ('pending', 'waiting_prediction', 'retry', 'sending')
              AND julianday(kickoff_at) <= julianday(?)
            """,
            (now_iso, now_iso),
        )
    expired = int(expired_cursor.rowcount)
    rows = connection.execute(
        """
        SELECT *
        FROM notification_deliveries
        WHERE status IN ('pending', 'waiting_prediction', 'retry')
          AND julianday(next_attempt_at) <= julianday(?)
          AND julianday(kickoff_at) > julianday(?)
        ORDER BY julianday(scheduled_for), id
        """,
        (now_iso, now_iso),
    ).fetchall()
    sent = 0
    retried = 0
    failed = 0

    for row in rows:
        if str(row["channel"]) not in configured_channel_set:
            continue
        with connection:
            claimed = connection.execute(
                """
                UPDATE notification_deliveries
                SET status = 'sending',
                    updated_at = ?
                WHERE id = ?
                  AND status IN ('pending', 'waiting_prediction', 'retry')
                """,
                (now_iso, int(row["id"])),
            )
        if claimed.rowcount == 0:
            continue

        notification_type = str(
            row["notification_type"] or PREDICTION_WINDOW_NOTIFICATION
        )
        if notification_type == OFFICIAL_LINEUP_NOTIFICATION:
            official = _official_lineup_payload(
                connection,
                str(row["match_id"]),
                str(row["team_norm"] or ""),
                str(row["lineup_hash"] or ""),
                local_now,
                str(row["window_label"]),
            )
            if official["status"] == "lineup_changed":
                with connection:
                    connection.execute(
                        """
                        UPDATE notification_deliveries
                        SET status = 'superseded',
                            error_code = 'official_lineup_updated',
                            updated_at = ?
                        WHERE id = ?
                        """,
                        (now_iso, int(row["id"])),
                    )
                continue
            prediction = (
                None
                if official["status"] == "prediction_missing"
                else (official.get("prediction_id"), official.get("payload"))
                if official["status"] == "ready"
                else None
            )
        else:
            prediction = _latest_prediction_payload(
                connection,
                str(row["match_id"]),
                local_now,
                str(row["window_label"]),
            )
        if prediction is None:
            next_attempt = min(
                local_now + timedelta(minutes=1),
                _as_datetime(str(row["kickoff_at"]), settings.local_timezone),
            )
            with connection:
                connection.execute(
                    """
                    UPDATE notification_deliveries
                    SET status = 'waiting_prediction',
                        next_attempt_at = ?,
                        error_code = 'prediction_not_available',
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (_utc_iso(next_attempt), now_iso, int(row["id"])),
                )
            continue

        prediction_id, payload = prediction
        attempt_count = int(row["attempt_count"]) + 1
        session = (sessions or {}).get(str(row["channel"]))
        result = _adapter(str(row["channel"]), settings, session).send(
            payload,
            local_now,
        )
        if result.success:
            with connection:
                connection.execute(
                    """
                    UPDATE notification_deliveries
                    SET prediction_id = ?,
                        payload_json = ?,
                        status = 'sent',
                        attempt_count = ?,
                        last_attempt_at = ?,
                        sent_at = ?,
                        last_http_status = ?,
                        error_code = NULL,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        prediction_id,
                        json.dumps(payload, ensure_ascii=False),
                        attempt_count,
                        now_iso,
                        now_iso,
                        result.status_code,
                        now_iso,
                        int(row["id"]),
                    ),
                )
            sent += 1
            continue

        can_retry = result.retryable and attempt_count <= len(RETRY_DELAYS_MINUTES)
        if can_retry:
            delay_seconds = RETRY_DELAYS_MINUTES[attempt_count - 1] * 60
            if result.retry_after_seconds is not None:
                delay_seconds = max(delay_seconds, result.retry_after_seconds)
            next_attempt = local_now + timedelta(seconds=delay_seconds)
            kickoff = _as_datetime(str(row["kickoff_at"]), settings.local_timezone)
            if next_attempt >= kickoff:
                status = "expired"
                error_code = "retry_after_kickoff"
                expired += 1
            else:
                status = "retry"
                error_code = result.error_code
                retried += 1
        else:
            next_attempt = local_now
            status = "failed"
            error_code = result.error_code or "delivery_failed"
            failed += 1
        with connection:
            connection.execute(
                """
                UPDATE notification_deliveries
                SET prediction_id = ?,
                    payload_json = ?,
                    status = ?,
                    attempt_count = ?,
                    next_attempt_at = ?,
                    last_attempt_at = ?,
                    last_http_status = ?,
                    error_code = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    prediction_id,
                    json.dumps(payload, ensure_ascii=False),
                    status,
                    attempt_count,
                    _utc_iso(next_attempt),
                    now_iso,
                    result.status_code,
                    error_code,
                    now_iso,
                    int(row["id"]),
                ),
            )

    return {
        "enabled": True,
        "sent": sent,
        "retry": retried,
        "failed": failed,
        "expired": expired,
        "configuration_errors": configuration_errors,
    }


def run_notification_cycle(
    connection: sqlite3.Connection,
    now: datetime | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    schedule = schedule_due_notifications(
        connection,
        now=now,
        settings=settings,
    )
    delivery = dispatch_notifications(
        connection,
        now=now,
        settings=settings,
    )
    if settings.notifications_enabled:
        from quiniela.output_manager import write_automation_status

        write_automation_status(connection)
    return {"schedule": schedule, "delivery": delivery}


def test_notifications(
    channel: str = "all",
    settings: Settings | None = None,
    sessions: dict[str, requests.Session] | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    requested = ["ntfy", "discord"] if channel == "all" else [channel]
    configured, configuration_errors = _configured_channels(settings)
    now = datetime.now(ZoneInfo(settings.local_timezone))
    payload = {
        "title": "Prueba de notificaciones - Quiniela Mundial 2026",
        "message": (
            "La configuracion funciona correctamente.\n"
            "Este mensaje no corresponde a un partido real."
        ),
        "window_label": "test",
        "kickoff": "Mensaje de prueba",
        "poisson": "1-0 (prueba)",
        "hybrid": "1-1 (prueba)",
        "outcomes": "1 40.0% | X 30.0% | 2 30.0%",
        "lineups": "datos de prueba",
        "freshness": now.isoformat(),
        "models": "prueba local",
    }
    results: dict[str, Any] = {}
    for requested_channel in requested:
        if requested_channel not in {"ntfy", "discord"}:
            raise ValueError("El canal debe ser ntfy, discord o all.")
        if requested_channel not in configured:
            results[requested_channel] = {
                "success": False,
                "error_code": f"{requested_channel}_not_configured",
            }
            continue
        result = _adapter(
            requested_channel,
            settings,
            (sessions or {}).get(requested_channel),
        ).send(payload, now)
        results[requested_channel] = {
            "success": result.success,
            "status_code": result.status_code,
            "retryable": result.retryable,
            "error_code": result.error_code,
        }
    return {
        "results": results,
        "configuration_errors": configuration_errors,
    }
