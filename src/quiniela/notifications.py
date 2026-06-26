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
from quiniela.infrastructure.competition_config import (
    CompetitionContext,
    resolve_competition_context,
)
from quiniela.name_maps import normalize_team_name, normalize_text


NOTIFICATION_WINDOWS = (15, 5)
RETRY_DELAYS_MINUTES = (1, 2, 4, 5)
OPEN_STATUSES = ("pending", "waiting_prediction", "retry", "sending")
TERMINAL_MATCH_STATUSES = {"FT", "AET", "PEN", "PST", "CANC", "ABD", "AWD", "WO"}
PREDICTION_WINDOW_NOTIFICATION = "prediction_window"
OFFICIAL_LINEUP_NOTIFICATION = "official_lineup"
OFFICIAL_LINEUP_EXPIRATION_GRACE_MINUTES = 120
NOTIFICATION_TEMPLATE_VERSION = "notification_v1"
DEFAULT_MAX_ATTEMPTS = len(RETRY_DELAYS_MINUTES) + 1


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


def _payload_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _notification_dedupe_key(
    *,
    notification_type: str,
    match_id: str,
    kickoff_at: str,
    window_label: str,
    channel: str,
    team_norm: str | None = None,
) -> str:
    parts = [notification_type, match_id, kickoff_at, window_label, channel]
    if team_norm:
        parts.append(team_norm)
    return ":".join(parts)


def _channel_priority(notification_type: str, window_label: str) -> str:
    if notification_type == OFFICIAL_LINEUP_NOTIFICATION:
        return "4"
    return "5" if window_label == "t-5" else "4"


def _expires_at(notification_type: str, kickoff: datetime) -> str:
    if notification_type == OFFICIAL_LINEUP_NOTIFICATION:
        return _utc_iso(kickoff + timedelta(minutes=OFFICIAL_LINEUP_EXPIRATION_GRACE_MINUTES))
    return _utc_iso(kickoff)


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
    competition_context: CompetitionContext | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    context = competition_context or resolve_competition_context()
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
            dedupe_key = _notification_dedupe_key(
                notification_type=OFFICIAL_LINEUP_NOTIFICATION,
                match_id=match_id,
                kickoff_at=kickoff_at,
                window_label=window_label,
                channel=channel,
                team_norm=team_norm,
            )
            kickoff = _as_datetime(kickoff_at, settings.local_timezone)
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
                      AND competition_id = ?
                      AND season_id = ?
                      AND status IN ('pending', 'waiting_prediction', 'retry', 'sending')
                      AND lineup_hash <> ?
                    """,
                    (
                        now_iso,
                        OFFICIAL_LINEUP_NOTIFICATION,
                        match_id,
                        channel,
                        team_norm,
                        context.competition_id,
                        context.season_id,
                        lineup_hash,
                    ),
                ).rowcount
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO notification_deliveries (
                        competition_id, season_id,
                        match_id, kickoff_at, window_label, channel,
                        notification_type, team_norm, lineup_hash, dedupe_key,
                        max_attempts, template_version, expires_at, channel_priority,
                        scheduled_for, status, next_attempt_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
                    """,
                    (
                        context.competition_id,
                        context.season_id,
                        match_id,
                        kickoff_at,
                        window_label,
                        channel,
                        OFFICIAL_LINEUP_NOTIFICATION,
                        team_norm,
                        lineup_hash,
                        dedupe_key,
                        DEFAULT_MAX_ATTEMPTS,
                        NOTIFICATION_TEMPLATE_VERSION,
                        _expires_at(OFFICIAL_LINEUP_NOTIFICATION, kickoff),
                        _channel_priority(OFFICIAL_LINEUP_NOTIFICATION, window_label),
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
    competition_context: CompetitionContext | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    context = competition_context or resolve_competition_context()
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
          AND competition_id = ?
          AND season_id = ?
        ORDER BY julianday(datetime_cdmx)
        """,
        (now_iso, context.competition_id, context.season_id),
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
                  AND competition_id = ?
                  AND season_id = ?
                  AND status IN ('pending', 'waiting_prediction', 'retry', 'sending')
                """,
                (
                    now_iso,
                    str(match["match_id"]),
                    kickoff_at,
                    context.competition_id,
                    context.season_id,
                ),
            )

        open_rows = connection.execute(
            """
            SELECT id, window_label
            FROM notification_deliveries
            WHERE match_id = ?
              AND kickoff_at = ?
              AND notification_type = ?
              AND competition_id = ?
              AND season_id = ?
              AND status IN ('pending', 'waiting_prediction', 'retry', 'sending')
            """,
            (
                str(match["match_id"]),
                kickoff_at,
                PREDICTION_WINDOW_NOTIFICATION,
                context.competition_id,
                context.season_id,
            ),
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
            dedupe_key = _notification_dedupe_key(
                notification_type=PREDICTION_WINDOW_NOTIFICATION,
                match_id=str(match["match_id"]),
                kickoff_at=kickoff_at,
                window_label=window_label,
                channel=channel,
            )
            with connection:
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO notification_deliveries (
                        competition_id, season_id,
                        match_id, kickoff_at, window_label, channel,
                        notification_type, dedupe_key, max_attempts,
                        template_version, expires_at, channel_priority,
                        scheduled_for, status, next_attempt_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
                    """,
                    (
                        context.competition_id,
                        context.season_id,
                        str(match["match_id"]),
                        kickoff_at,
                        window_label,
                        channel,
                        PREDICTION_WINDOW_NOTIFICATION,
                        dedupe_key,
                        DEFAULT_MAX_ATTEMPTS,
                        NOTIFICATION_TEMPLATE_VERSION,
                        _expires_at(PREDICTION_WINDOW_NOTIFICATION, kickoff),
                        _channel_priority(PREDICTION_WINDOW_NOTIFICATION, window_label),
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
        "web_estimated_lineup": "estimada por noticias",
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
            m.home_team_norm,
            m.away_team_norm,
            m.api_fixture_id,
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


def _lineup_payload_status(
    connection: sqlite3.Connection,
    fixture_id: Any,
    team_norm: str,
) -> str | None:
    if fixture_id in (None, "") or str(fixture_id) == "nan" or not team_norm:
        return None
    fixture_key = str(int(fixture_id))
    official_row = connection.execute(
        """
        SELECT source_json
        FROM historical_lineups
        WHERE fixture_id = ? AND team_norm = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (fixture_key, team_norm),
    ).fetchone()
    if official_row is not None:
        payload = json.loads(official_row["source_json"] or "{}")
        if len(payload.get("startXI") or []) >= 11:
            return "confirmed_lineup"

    estimate_row = connection.execute(
        """
        SELECT source_json
        FROM lineup_estimates
        WHERE fixture_id = ? AND team_norm = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (fixture_key, team_norm),
    ).fetchone()
    if estimate_row is not None:
        payload = json.loads(estimate_row["source_json"] or "{}")
        if len(payload.get("startXI") or []) >= 11:
            return "web_estimated"
    return None


def _current_lineup_source(
    connection: sqlite3.Connection,
    row: sqlite3.Row,
    team_field: str,
    team_norm_field: str,
    fallback_source: str | None,
) -> str | None:
    team_norm = str(row[team_norm_field] or normalize_team_name(str(row[team_field])) or "")
    live_source = _lineup_payload_status(
        connection,
        row["api_fixture_id"],
        team_norm,
    )
    return live_source or fallback_source


def _kickoff_label(row: sqlite3.Row, timezone_name: str) -> str:
    kickoff = _as_datetime(str(row["datetime_cdmx"]), timezone_name)
    return (
        f"Inicio CDMX: {kickoff.strftime('%Y-%m-%d %H:%M')} "
        f"({timezone_name})"
    )


def _prediction_summary_from_row(
    connection: sqlite3.Connection,
    row: sqlite3.Row,
    timezone_name: str,
) -> dict[str, str]:
    source = json.loads(row["source_json"] or "{}")
    home_lineup_source = _current_lineup_source(
        connection,
        row,
        "home_team",
        "home_team_norm",
        source.get("home_lineup_source"),
    )
    away_lineup_source = _current_lineup_source(
        connection,
        row,
        "away_team",
        "away_team_norm",
        source.get("away_lineup_source"),
    )
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
        f"{row['home_team']}: {_lineup_label(home_lineup_source)}; "
        f"{row['away_team']}: {_lineup_label(away_lineup_source)}"
    )
    freshness = str(row["data_freshness_at"] or "no disponible")
    models = (
        f"{row['model_version']} / "
        f"{row['outcome_model_version'] or 'Poisson fallback'}"
    )
    kickoff = _kickoff_label(row, timezone_name)
    return {
        "poisson": poisson,
        "hybrid": hybrid,
        "outcomes": outcomes,
        "lineups": lineups,
        "freshness": freshness,
        "models": models,
        "kickoff": kickoff,
    }


def _market_summary_from_source(source_json: str | None) -> str:
    source = json.loads(source_json or "{}")
    parts: list[str] = []
    exact_score = source.get("odds_adjusted_exact_score")
    exact_probability = source.get("odds_adjusted_probability")
    if exact_score:
        probability = (
            f" ({float(exact_probability) * 100:.1f}%)"
            if exact_probability is not None
            else ""
        )
        parts.append(f"odds-aware {exact_score}{probability}")
    markets = (source.get("odds_consensus") or {}).get("markets") or {}
    one_x_two = markets.get("match_winner") or {}
    if one_x_two:
        values = []
        for key, label in (("home", "1"), ("draw", "X"), ("away", "2")):
            item = one_x_two.get(key) or {}
            probability = item.get("probability")
            if probability is not None:
                values.append(f"{label} {float(probability) * 100:.1f}%")
        if values:
            parts.append("mercado 1-X-2 " + " | ".join(values))
    return " | ".join(parts) if parts else "momios prepartido pendientes"


def _latest_prediction_row_for_test(
    connection: sqlite3.Connection,
    match_id: str,
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
            m.home_team_norm,
            m.away_team_norm,
            m.api_fixture_id,
            m.datetime_cdmx,
            m.time_cdmx
        FROM predictions p
        INNER JOIN matches m ON m.match_id = p.match_id
        WHERE p.match_id = ?
        ORDER BY julianday(COALESCE(p.generated_at_utc, p.generated_at)) DESC,
                 p.id DESC
        LIMIT 1
        """,
        (match_id,),
    ).fetchone()


def _synthetic_starters(
    connection: sqlite3.Connection,
    team_norm: str,
    team_name: str,
    competition_context: CompetitionContext,
) -> list[str]:
    rows = connection.execute(
        """
        SELECT player, position_group
        FROM players
        WHERE team_norm = ?
          AND competition_id = ?
          AND season_id = ?
          AND COALESCE(is_active, 1) = 1
        ORDER BY
          CASE position_group
            WHEN 'goalkeeper' THEN 0
            WHEN 'defender' THEN 1
            WHEN 'midfielder' THEN 2
            WHEN 'forward' THEN 3
            ELSE 4
          END,
          player
        LIMIT 11
        """,
        (
            team_norm,
            competition_context.competition_id,
            competition_context.season_id,
        ),
    ).fetchall()
    starters = [str(row["player"]) for row in rows if row["player"]]
    while len(starters) < 11:
        starters.append(f"{team_name} Test Player {len(starters) + 1}")
    return starters[:11]


def send_isolated_lineup_test_notifications(
    *,
    date_str: str,
    connection: sqlite3.Connection | None = None,
    settings: Settings | None = None,
    session: requests.Session | None = None,
    send: bool = False,
    competition_context: CompetitionContext | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    context = competition_context or resolve_competition_context()
    connection = connection or get_connection(settings.db_path)
    now = datetime.now(ZoneInfo(settings.local_timezone))
    if not settings.ntfy_enabled or not settings.ntfy_topic:
        return {
            "enabled": False,
            "sent": 0,
            "planned": 0,
            "error_code": "ntfy_not_configured",
        }

    matches = connection.execute(
        """
        SELECT match_id, home_team, away_team, home_team_norm, away_team_norm,
               time_cdmx, api_fixture_id
        FROM matches
        WHERE date_cdmx = ?
          AND competition_id = ?
          AND season_id = ?
        ORDER BY datetime_cdmx, home_team
        """,
        (date_str, context.competition_id, context.season_id),
    ).fetchall()
    adapter = NtfyAdapter(settings, session=session)
    sent = 0
    failed = 0
    planned_payloads: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    for match in matches:
        prediction_row = _latest_prediction_row_for_test(connection, str(match["match_id"]))
        if prediction_row is not None:
            summary = _prediction_summary_from_row(
                connection,
                prediction_row,
                settings.local_timezone,
            )
            market_summary = _market_summary_from_source(prediction_row["source_json"])
        else:
            kickoff = _as_datetime(
                f"{date_str}T{match['time_cdmx']}:00",
                settings.local_timezone,
            )
            summary = {
                "poisson": "pendiente",
                "hybrid": "pendiente",
                "outcomes": "pendiente",
                "freshness": "sin prediccion disponible",
                "models": "n/d",
                "kickoff": (
                    f"Inicio CDMX: {kickoff.strftime('%Y-%m-%d %H:%M')} "
                    f"({settings.local_timezone})"
                ),
            }
            market_summary = "momios prepartido pendientes"

        teams = (
            (str(match["home_team"]), str(match["home_team_norm"]), str(match["away_team"])),
            (str(match["away_team"]), str(match["away_team_norm"]), str(match["home_team"])),
        )
        fixture_note = (
            f"Fixture API: {int(match['api_fixture_id'])}"
            if match["api_fixture_id"] not in (None, "")
            else "Fixture API pendiente"
        )
        for team_name, team_norm, opponent in teams:
            starters = _synthetic_starters(
                connection,
                team_norm,
                team_name,
                context,
            )
            payload = {
                "title": f"[PRUEBA AISLADA] Alineacion oficial: {team_name}",
                "message": "\n".join(
                    [
                        "PRUEBA AISLADA - no corresponde a una alineacion real.",
                        "XI sintetico de prueba; no usar como alineacion real.",
                        summary["kickoff"],
                        fixture_note,
                        f"Rival: {opponent}",
                        f"XI sintetico {team_name}: {', '.join(starters)}",
                        f"Mini pronostico: Poisson {summary['poisson']}",
                        f"Hibrido: {summary['hybrid']}",
                        f"1-X-2: {summary['outcomes']}",
                        f"Mercado: {market_summary}",
                    ]
                ),
                "window_label": "isolated-lineup-test",
                "notification_type": "isolated_lineup_test",
                "priority": "4",
            }
            planned_payloads.append(
                {
                    "match_id": str(match["match_id"]),
                    "team_norm": team_norm,
                    "title": payload["title"],
                }
            )
            if not send:
                continue
            result = adapter.send(payload, now)
            if result.success:
                sent += 1
            else:
                failed += 1
                errors.append(
                    {
                        "match_id": str(match["match_id"]),
                        "team_norm": team_norm,
                        "status_code": result.status_code,
                        "error_code": result.error_code,
                        "retryable": result.retryable,
                    }
                )

    return {
        "enabled": True,
        "date": date_str,
        "mode": "synthetic_isolated",
        "send": send,
        "matches": len(matches),
        "planned": len(planned_payloads),
        "sent": sent,
        "failed": failed,
        "errors": errors,
        "deliveries_mutated": False,
        "planned_payloads": planned_payloads,
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

    summary = _prediction_summary_from_row(
        connection,
        row,
        get_settings().local_timezone,
    )
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

    home_norm = normalize_team_name(str(match["home_team"]))
    away_norm = normalize_team_name(str(match["away_team"]))
    if team_norm in {str(match["home_team_norm"]), home_norm}:
        team_name = str(match["home_team"])
        opponent = str(match["away_team"])
    elif team_norm in {str(match["away_team_norm"]), away_norm}:
        team_name = str(match["away_team"])
        opponent = str(match["home_team"])
    else:
        return {"status": "lineup_missing"}
    starters = _starting_xi_names(lineup_payload)
    summary = _prediction_summary_from_row(
        connection,
        prediction_row,
        get_settings().local_timezone,
    )
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
    competition_context: CompetitionContext | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    context = competition_context or resolve_competition_context()
    connection = connection or get_connection(settings.db_path)
    if not settings.notifications_enabled:
        return {"enabled": False, "sent": 0, "retry": 0, "failed": 0, "expired": 0}

    configured_channels, configuration_errors = _configured_channels(settings)
    configured_channel_set = set(configured_channels)
    local_tz = ZoneInfo(settings.local_timezone)
    local_now = now.astimezone(local_tz) if now else datetime.now(local_tz)
    now_iso = _utc_iso(local_now)
    stale_sending = _utc_iso(local_now - timedelta(minutes=10))
    official_expiry_before = _utc_iso(
        local_now - timedelta(minutes=OFFICIAL_LINEUP_EXPIRATION_GRACE_MINUTES)
    )
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
              AND competition_id = ?
              AND season_id = ?
            """,
            (
                now_iso,
                now_iso,
                stale_sending,
                context.competition_id,
                context.season_id,
            ),
        )
        expired_cursor = connection.execute(
            """
            UPDATE notification_deliveries
            SET status = 'expired',
                error_code = 'kickoff_reached',
                updated_at = ?
            WHERE status IN ('pending', 'waiting_prediction', 'retry', 'sending')
              AND competition_id = ?
              AND season_id = ?
              AND (
                  (
                      COALESCE(notification_type, ?) = ?
                      AND julianday(kickoff_at) <= julianday(?)
                  )
                  OR (
                      COALESCE(notification_type, ?) <> ?
                      AND julianday(kickoff_at) <= julianday(?)
                  )
              )
            """,
            (
                now_iso,
                context.competition_id,
                context.season_id,
                PREDICTION_WINDOW_NOTIFICATION,
                OFFICIAL_LINEUP_NOTIFICATION,
                official_expiry_before,
                PREDICTION_WINDOW_NOTIFICATION,
                OFFICIAL_LINEUP_NOTIFICATION,
                now_iso,
            ),
        )
    expired = int(expired_cursor.rowcount)
    rows = connection.execute(
        """
        SELECT *
        FROM notification_deliveries
        WHERE status IN ('pending', 'waiting_prediction', 'retry')
          AND competition_id = ?
          AND season_id = ?
          AND julianday(next_attempt_at) <= julianday(?)
          AND (
              (
                  COALESCE(notification_type, ?) = ?
                  AND julianday(kickoff_at) > julianday(?)
              )
              OR (
                  COALESCE(notification_type, ?) <> ?
                  AND julianday(kickoff_at) > julianday(?)
              )
          )
        ORDER BY julianday(scheduled_for), id
        """,
        (
            context.competition_id,
            context.season_id,
            now_iso,
            PREDICTION_WINDOW_NOTIFICATION,
            OFFICIAL_LINEUP_NOTIFICATION,
            official_expiry_before,
            PREDICTION_WINDOW_NOTIFICATION,
            OFFICIAL_LINEUP_NOTIFICATION,
            now_iso,
        ),
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
                        payload_hash = ?,
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
                        _payload_hash(payload),
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
                    payload_hash = ?,
                    status = ?,
                    attempt_count = ?,
                    next_attempt_at = ?,
                    last_attempt_at = ?,
                    last_http_status = ?,
                    error_code = ?,
                    last_error_message = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    prediction_id,
                    json.dumps(payload, ensure_ascii=False),
                    _payload_hash(payload),
                    status,
                    attempt_count,
                    _utc_iso(next_attempt),
                    now_iso,
                    result.status_code,
                    error_code,
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


def notification_status(
    connection: sqlite3.Connection | None = None,
    *,
    settings: Settings | None = None,
    include_recent: int = 10,
    competition_context: CompetitionContext | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    context = competition_context or resolve_competition_context()
    connection = connection or get_connection(settings.db_path)
    rows = connection.execute(
        """
        SELECT status, channel, COALESCE(notification_type, ?) AS notification_type, COUNT(*) AS total
        FROM notification_deliveries
        WHERE competition_id = ?
          AND season_id = ?
        GROUP BY status, channel, COALESCE(notification_type, ?)
        ORDER BY status, channel, notification_type
        """,
        (
            PREDICTION_WINDOW_NOTIFICATION,
            context.competition_id,
            context.season_id,
            PREDICTION_WINDOW_NOTIFICATION,
        ),
    ).fetchall()
    recent = connection.execute(
        """
        SELECT id, match_id, kickoff_at, window_label, channel,
               COALESCE(notification_type, ?) AS notification_type,
               status, attempt_count, next_attempt_at, error_code,
               dedupe_key, payload_hash, expires_at
        FROM notification_deliveries
        WHERE competition_id = ?
          AND season_id = ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (
            PREDICTION_WINDOW_NOTIFICATION,
            context.competition_id,
            context.season_id,
            include_recent,
        ),
    ).fetchall()
    configured_channels, configuration_errors = _configured_channels(settings)
    return {
        "enabled": settings.notifications_enabled,
        "configured_channels": configured_channels,
        "configuration_errors": configuration_errors,
        "competition_id": context.competition_id,
        "season_id": context.season_id,
        "counts": [dict(row) for row in rows],
        "recent": [dict(row) for row in recent],
    }


def retry_failed_notifications(
    connection: sqlite3.Connection | None = None,
    *,
    now: datetime | None = None,
    settings: Settings | None = None,
    apply: bool = False,
    competition_context: CompetitionContext | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    context = competition_context or resolve_competition_context()
    connection = connection or get_connection(settings.db_path)
    local_tz = ZoneInfo(settings.local_timezone)
    local_now = now.astimezone(local_tz) if now else datetime.now(local_tz)
    now_iso = _utc_iso(local_now)
    candidates = connection.execute(
        """
        SELECT id, match_id, channel, notification_type, kickoff_at, expires_at, error_code
        FROM notification_deliveries
        WHERE status = 'failed'
          AND competition_id = ?
          AND season_id = ?
          AND julianday(COALESCE(expires_at, kickoff_at)) > julianday(?)
        ORDER BY id
        """,
        (context.competition_id, context.season_id, now_iso),
    ).fetchall()
    if apply and candidates:
        ids = [int(row["id"]) for row in candidates]
        placeholders = ",".join("?" for _ in ids)
        with connection:
            connection.execute(
                f"""
                UPDATE notification_deliveries
                SET status = 'retry',
                    next_attempt_at = ?,
                    error_code = 'manual_retry_requested',
                    last_error_message = NULL,
                    updated_at = ?
                WHERE id IN ({placeholders})
                """,
                (now_iso, now_iso, *ids),
            )
    return {
        "apply": apply,
        "matched": len(candidates),
        "updated": len(candidates) if apply else 0,
        "candidates": [dict(row) for row in candidates],
    }


def dry_run_notifications(
    connection: sqlite3.Connection | None = None,
    *,
    now: datetime | None = None,
    settings: Settings | None = None,
    competition_context: CompetitionContext | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    context = competition_context or resolve_competition_context()
    connection = connection or get_connection(settings.db_path)
    local_tz = ZoneInfo(settings.local_timezone)
    local_now = now.astimezone(local_tz) if now else datetime.now(local_tz)
    now_iso = _utc_iso(local_now)
    configured_channels, configuration_errors = _configured_channels(settings)
    due_rows = connection.execute(
        """
        SELECT id, match_id, window_label, channel,
               COALESCE(notification_type, ?) AS notification_type,
               status, attempt_count, next_attempt_at, expires_at, error_code
        FROM notification_deliveries
        WHERE status IN ('pending', 'waiting_prediction', 'retry')
          AND competition_id = ?
          AND season_id = ?
          AND julianday(next_attempt_at) <= julianday(?)
        ORDER BY julianday(scheduled_for), id
        """,
        (
            PREDICTION_WINDOW_NOTIFICATION,
            context.competition_id,
            context.season_id,
            now_iso,
        ),
    ).fetchall()
    return {
        "enabled": settings.notifications_enabled,
        "now": now_iso,
        "configured_channels": configured_channels,
        "configuration_errors": configuration_errors,
        "competition_id": context.competition_id,
        "season_id": context.season_id,
        "due_count": len(due_rows),
        "would_send": [
            dict(row)
            for row in due_rows
            if str(row["channel"]) in set(configured_channels)
        ],
        "skipped": [
            dict(row)
            for row in due_rows
            if str(row["channel"]) not in set(configured_channels)
        ],
    }


def explain_notification(
    notification_id: int,
    connection: sqlite3.Connection | None = None,
    *,
    settings: Settings | None = None,
    competition_context: CompetitionContext | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    context = competition_context or resolve_competition_context()
    connection = connection or get_connection(settings.db_path)
    row = connection.execute(
        """
        SELECT *
        FROM notification_deliveries
        WHERE id = ?
          AND competition_id = ?
          AND season_id = ?
        """,
        (notification_id, context.competition_id, context.season_id),
    ).fetchone()
    if row is None:
        return {"found": False, "id": notification_id}
    payload = {}
    if row["payload_json"]:
        try:
            payload = json.loads(row["payload_json"])
        except json.JSONDecodeError:
            payload = {"unparseable": True}
    return {
        "found": True,
        "delivery": {
            key: row[key]
            for key in row.keys()
            if key not in {"payload_json"}
        },
        "payload_summary": {
            "title": payload.get("title"),
            "window_label": payload.get("window_label"),
            "notification_type": payload.get("notification_type"),
            "has_message": bool(payload.get("message")),
            "message_preview": str(payload.get("message") or "")[:240],
        },
        "secrets_included": False,
        "local_timezone": settings.local_timezone,
    }


def run_notification_cycle(
    connection: sqlite3.Connection,
    now: datetime | None = None,
    settings: Settings | None = None,
    competition_context: CompetitionContext | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    context = competition_context or resolve_competition_context()
    schedule = schedule_due_notifications(
        connection,
        now=now,
        settings=settings,
        competition_context=context,
    )
    delivery = dispatch_notifications(
        connection,
        now=now,
        settings=settings,
        competition_context=context,
    )
    if settings.notifications_enabled:
        from quiniela.output_manager import write_automation_status

        write_automation_status(connection, competition_context=context)
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
