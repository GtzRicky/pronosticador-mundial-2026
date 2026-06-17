from __future__ import annotations

from dataclasses import replace
from datetime import datetime
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from quiniela.config import get_settings
import quiniela.historical_loader as historical_loader
import quiniela.notifications as notifications_module
from quiniela.db import get_connection
from quiniela.notifications import (
    dispatch_notifications,
    queue_official_lineup_notifications,
    schedule_due_notifications,
    test_notifications as send_test_notifications,
)


TZ = ZoneInfo("America/Mexico_City")


class FakeResponse:
    def __init__(self, status_code: int, headers: dict[str, str] | None = None):
        self.status_code = status_code
        self.headers = headers or {}


class FakeSession:
    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def _settings(**overrides):
    defaults = {
        "notifications_enabled": True,
        "ntfy_enabled": True,
        "ntfy_server_url": "https://ntfy.sh",
        "ntfy_topic": "private-random-topic",
        "discord_enabled": True,
        "discord_webhook_url": "https://discord.test/api/webhooks/secret",
        "notification_timeout_seconds": 3.0,
    }
    defaults.update(overrides)
    return replace(get_settings(), **defaults)


def _seed_match(connection, kickoff: str = "2026-06-13T13:00:00-06:00") -> None:
    with connection:
        connection.execute(
            """
            INSERT INTO matches (
                match_id, date_et, time_et, datetime_et,
                date_cdmx, time_cdmx, datetime_cdmx,
                home_team, away_team, home_team_norm, away_team_norm,
                group_name, stadium, stage, status, api_fixture_id
            ) VALUES (
                'match-1', '2026-06-13', '15:00', '2026-06-13T15:00:00-04:00',
                '2026-06-13', '13:00', ?,
                'Mexico', 'Canada', 'mexico', 'canada',
                'A', 'Test Stadium', 'Group', 'NS', 123
            )
            """,
            (kickoff,),
        )


def _seed_prediction(
    connection,
    generated_at: str = "2026-06-13T18:40:00Z",
    hybrid: bool = True,
) -> None:
    with connection:
        connection.execute(
            """
            INSERT INTO predictions (
                match_id, datetime_cdmx, group_name,
                home_team, away_team, predicted_score, probability,
                model_version, hybrid_predicted_score, hybrid_probability,
                home_win_probability, draw_probability, away_win_probability,
                outcome_model_version, data_freshness_at, source_json,
                prediction_context, window_label, generated_at_utc,
                is_pre_kickoff, generated_at
            ) VALUES (
                'match-1', '2026-06-13T13:00:00-06:00', 'A',
                'Mexico', 'Canada', '2-1', 0.21,
                'poisson_player_v1', ?, ?,
                0.52, 0.27, 0.21,
                'logit_outcome_v1', '2026-06-13T18:39:00Z', ?,
                'pre_match', 't-15', ?, 1, ?
            )
            """,
            (
                "2-1" if hybrid else None,
                0.24 if hybrid else None,
                json.dumps(
                    {
                        "home_lineup_source": "confirmed_lineup",
                        "away_lineup_source": "web_estimated",
                    }
                ),
                generated_at,
                generated_at,
            ),
        )


def _official_lineup_payload(
    team_name: str,
    starters: list[str] | None = None,
) -> dict:
    starters = starters or [f"{team_name} Player {index}" for index in range(1, 12)]
    return {
        "team": {"name": team_name},
        "startXI": [
            {"player": {"name": player_name, "number": index}}
            for index, player_name in enumerate(starters, start=1)
        ],
        "substitutes": [],
    }


def _store_official_lineup(
    connection,
    *,
    fixture_id: str = "123",
    team_norm: str,
    payload: dict,
) -> None:
    with connection:
        connection.execute(
            """
            INSERT INTO historical_lineups (fixture_id, team_norm, source_json)
            VALUES (?, ?, ?)
            ON CONFLICT(fixture_id, team_norm) DO UPDATE SET
                source_json = excluded.source_json,
                fetched_at = CURRENT_TIMESTAMP
            """,
            (fixture_id, team_norm, json.dumps(payload)),
        )


def test_late_restart_schedules_only_latest_due_window(tmp_path: Path) -> None:
    connection = get_connection(tmp_path / "notifications.sqlite")
    _seed_match(connection)
    settings = _settings()
    now = datetime(2026, 6, 13, 12, 56, tzinfo=TZ)

    first = schedule_due_notifications(connection, now=now, settings=settings)
    second = schedule_due_notifications(connection, now=now, settings=settings)

    rows = connection.execute(
        """
        SELECT window_label, channel
        FROM notification_deliveries
        ORDER BY channel
        """
    ).fetchall()
    assert first["scheduled"] == 2
    assert second["scheduled"] == 0
    assert {(row["window_label"], row["channel"]) for row in rows} == {
        ("t-5", "ntfy"),
        ("t-5", "discord"),
    }


def test_newer_window_supersedes_only_unsent_delivery(tmp_path: Path) -> None:
    connection = get_connection(tmp_path / "supersede.sqlite")
    _seed_match(connection)
    settings = _settings()
    schedule_due_notifications(
        connection,
        now=datetime(2026, 6, 13, 12, 46, tzinfo=TZ),
        settings=settings,
    )
    with connection:
        connection.execute(
            """
            UPDATE notification_deliveries
            SET status = 'sent'
            WHERE channel = 'ntfy'
            """
        )

    schedule_due_notifications(
        connection,
        now=datetime(2026, 6, 13, 12, 56, tzinfo=TZ),
        settings=settings,
    )

    rows = connection.execute(
        """
        SELECT window_label, channel, status
        FROM notification_deliveries
        ORDER BY id
        """
    ).fetchall()
    assert [(row["window_label"], row["channel"], row["status"]) for row in rows] == [
        ("t-15", "ntfy", "sent"),
        ("t-15", "discord", "superseded"),
        ("t-5", "ntfy", "pending"),
        ("t-5", "discord", "pending"),
    ]


def test_dispatch_sends_both_channels_without_persisting_secrets(tmp_path: Path) -> None:
    connection = get_connection(tmp_path / "dispatch.sqlite")
    _seed_match(connection)
    _seed_prediction(connection)
    settings = _settings()
    now = datetime(2026, 6, 13, 12, 46, tzinfo=TZ)
    schedule_due_notifications(connection, now=now, settings=settings)
    ntfy = FakeSession(FakeResponse(200))
    discord = FakeSession(FakeResponse(204))

    result = dispatch_notifications(
        connection,
        now=now,
        settings=settings,
        sessions={"ntfy": ntfy, "discord": discord},
    )

    rows = connection.execute(
        """
        SELECT channel, status, payload_json, error_code
        FROM notification_deliveries
        ORDER BY channel
        """
    ).fetchall()
    persisted = json.dumps([dict(row) for row in rows])
    assert result["sent"] == 2
    assert {row["status"] for row in rows} == {"sent"}
    assert "private-random-topic" not in persisted
    assert "discord.test" not in persisted
    assert ntfy.calls[0]["headers"]["Priority"] == "4"
    assert discord.calls[0]["json"]["allowed_mentions"] == {"parse": []}


def test_missing_prediction_waits_without_attempt_then_sends(tmp_path: Path) -> None:
    connection = get_connection(tmp_path / "waiting.sqlite")
    _seed_match(connection)
    settings = _settings(discord_enabled=False)
    first_now = datetime(2026, 6, 13, 12, 46, tzinfo=TZ)
    schedule_due_notifications(connection, now=first_now, settings=settings)
    session = FakeSession(FakeResponse(200))

    first = dispatch_notifications(
        connection,
        now=first_now,
        settings=settings,
        sessions={"ntfy": session},
    )
    waiting = connection.execute(
        "SELECT status, attempt_count FROM notification_deliveries"
    ).fetchone()
    assert first["sent"] == 0
    assert waiting["status"] == "waiting_prediction"
    assert waiting["attempt_count"] == 0
    assert not session.calls

    _seed_prediction(connection, generated_at="2026-06-13T18:46:30Z")
    second = dispatch_notifications(
        connection,
        now=datetime(2026, 6, 13, 12, 47, tzinfo=TZ),
        settings=settings,
        sessions={"ntfy": session},
    )
    assert second["sent"] == 1


def test_retry_after_and_channel_failures_are_independent(tmp_path: Path) -> None:
    connection = get_connection(tmp_path / "retry.sqlite")
    _seed_match(connection)
    _seed_prediction(connection, hybrid=False)
    settings = _settings()
    now = datetime(2026, 6, 13, 12, 46, tzinfo=TZ)
    schedule_due_notifications(connection, now=now, settings=settings)

    result = dispatch_notifications(
        connection,
        now=now,
        settings=settings,
        sessions={
            "ntfy": FakeSession(FakeResponse(429, {"Retry-After": "120"})),
            "discord": FakeSession(FakeResponse(204)),
        },
    )

    ntfy = connection.execute(
        """
        SELECT status, attempt_count, next_attempt_at, error_code
        FROM notification_deliveries
        WHERE channel = 'ntfy'
        """
    ).fetchone()
    discord = connection.execute(
        "SELECT status FROM notification_deliveries WHERE channel = 'discord'"
    ).fetchone()
    assert result["sent"] == 1
    assert result["retry"] == 1
    assert ntfy["status"] == "retry"
    assert ntfy["attempt_count"] == 1
    assert ntfy["next_attempt_at"] == "2026-06-13T18:48:00Z"
    assert ntfy["error_code"] == "http_429"
    assert discord["status"] == "sent"


def test_permanent_client_error_and_kickoff_expiration(tmp_path: Path) -> None:
    connection = get_connection(tmp_path / "expiration.sqlite")
    _seed_match(connection)
    _seed_prediction(connection)
    settings = _settings(discord_enabled=False)
    now = datetime(2026, 6, 13, 12, 56, tzinfo=TZ)
    schedule_due_notifications(connection, now=now, settings=settings)

    failed = dispatch_notifications(
        connection,
        now=now,
        settings=settings,
        sessions={"ntfy": FakeSession(FakeResponse(401))},
    )
    assert failed["failed"] == 1
    row = connection.execute(
        "SELECT status, error_code FROM notification_deliveries"
    ).fetchone()
    assert (row["status"], row["error_code"]) == ("failed", "http_401")

    with connection:
        connection.execute(
            """
            UPDATE notification_deliveries
            SET status = 'retry', next_attempt_at = '2026-06-13T18:59:00Z'
            """
        )
    expired = dispatch_notifications(
        connection,
        now=datetime(2026, 6, 13, 13, 0, tzinfo=TZ),
        settings=settings,
        sessions={"ntfy": FakeSession(requests.Timeout())},
    )
    assert expired["expired"] == 1
    assert connection.execute(
        "SELECT status FROM notification_deliveries"
    ).fetchone()["status"] == "expired"


def test_rescheduled_match_uses_new_kickoff_identity(tmp_path: Path) -> None:
    connection = get_connection(tmp_path / "rescheduled.sqlite")
    _seed_match(connection)
    settings = _settings(discord_enabled=False)
    schedule_due_notifications(
        connection,
        now=datetime(2026, 6, 13, 12, 56, tzinfo=TZ),
        settings=settings,
    )
    with connection:
        connection.execute(
            """
            UPDATE matches
            SET datetime_cdmx = '2026-06-13T14:00:00-06:00',
                time_cdmx = '14:00'
            WHERE match_id = 'match-1'
            """
        )

    schedule_due_notifications(
        connection,
        now=datetime(2026, 6, 13, 13, 56, tzinfo=TZ),
        settings=settings,
    )
    rows = connection.execute(
        """
        SELECT kickoff_at, status
        FROM notification_deliveries
        ORDER BY id
        """
    ).fetchall()
    assert rows[0]["status"] == "expired"
    assert rows[1]["status"] == "pending"
    assert rows[0]["kickoff_at"] != rows[1]["kickoff_at"]


def test_queue_official_lineup_deduplicates_same_hash(tmp_path: Path) -> None:
    connection = get_connection(tmp_path / "official-queue.sqlite")
    _seed_match(connection)
    settings = _settings()
    now = datetime(2026, 6, 13, 12, 35, tzinfo=TZ)
    payload = {"response": [_official_lineup_payload("Mexico")]}

    first = queue_official_lineup_notifications(
        connection,
        match_id="match-1",
        kickoff_at="2026-06-13T13:00:00-06:00",
        lineups_payload=payload,
        now=now,
        settings=settings,
    )
    second = queue_official_lineup_notifications(
        connection,
        match_id="match-1",
        kickoff_at="2026-06-13T13:00:00-06:00",
        lineups_payload=payload,
        now=now,
        settings=settings,
    )

    rows = connection.execute(
        """
        SELECT notification_type, team_norm, window_label, channel
        FROM notification_deliveries
        ORDER BY channel
        """
    ).fetchall()
    assert first["scheduled"] == 2
    assert second["scheduled"] == 0
    assert {(row["notification_type"], row["team_norm"], row["channel"]) for row in rows} == {
        ("official_lineup", "mexico", "discord"),
        ("official_lineup", "mexico", "ntfy"),
    }
    assert all(str(row["window_label"]).startswith("official:mexico:") for row in rows)


def test_queue_official_lineup_supersedes_pending_hash(tmp_path: Path) -> None:
    connection = get_connection(tmp_path / "official-supersede.sqlite")
    _seed_match(connection)
    settings = _settings()
    now = datetime(2026, 6, 13, 12, 35, tzinfo=TZ)

    first_payload = {"response": [_official_lineup_payload("Mexico")]}
    changed_payload = {
        "response": [
            _official_lineup_payload(
                "Mexico",
                starters=["Mexico Player 1"] + [f"Mexico Alt {index}" for index in range(2, 12)],
            )
        ]
    }
    queue_official_lineup_notifications(
        connection,
        match_id="match-1",
        kickoff_at="2026-06-13T13:00:00-06:00",
        lineups_payload=first_payload,
        now=now,
        settings=settings,
    )
    result = queue_official_lineup_notifications(
        connection,
        match_id="match-1",
        kickoff_at="2026-06-13T13:00:00-06:00",
        lineups_payload=changed_payload,
        now=now,
        settings=settings,
    )

    status_rows = connection.execute(
        """
        SELECT status
        FROM notification_deliveries
        WHERE notification_type = 'official_lineup'
        ORDER BY id
        """
    ).fetchall()
    assert result["scheduled"] == 2
    assert result["superseded"] == 2
    assert [row["status"] for row in status_rows] == [
        "superseded",
        "superseded",
        "pending",
        "pending",
    ]


def test_dispatch_sends_official_lineup_with_prediction_summary(tmp_path: Path) -> None:
    connection = get_connection(tmp_path / "official-dispatch.sqlite")
    _seed_match(connection)
    _seed_prediction(connection)
    settings = _settings(discord_enabled=False)
    now = datetime(2026, 6, 13, 12, 40, tzinfo=TZ)
    lineup_payload = _official_lineup_payload("Mexico")
    _store_official_lineup(
        connection,
        team_norm="mexico",
        payload=lineup_payload,
    )
    queue_official_lineup_notifications(
        connection,
        match_id="match-1",
        kickoff_at="2026-06-13T13:00:00-06:00",
        lineups_payload={"response": [lineup_payload]},
        now=now,
        settings=settings,
    )

    result = dispatch_notifications(
        connection,
        now=now,
        settings=settings,
        sessions={"ntfy": FakeSession(FakeResponse(200))},
    )

    row = connection.execute(
        """
        SELECT status, payload_json
        FROM notification_deliveries
        WHERE notification_type = 'official_lineup'
        """
    ).fetchone()
    assert result["sent"] == 1
    assert row["status"] == "sent"
    assert "Alineacion oficial: Mexico" in row["payload_json"]
    assert "Mexico Player 1" in row["payload_json"]
    assert "Mini pronostico" in row["payload_json"]


def test_changed_official_lineup_after_sent_creates_new_delivery(tmp_path: Path) -> None:
    connection = get_connection(tmp_path / "official-changed-after-sent.sqlite")
    _seed_match(connection)
    _seed_prediction(connection)
    settings = _settings(discord_enabled=False)
    now = datetime(2026, 6, 13, 12, 40, tzinfo=TZ)

    first_lineup = _official_lineup_payload("Mexico")
    _store_official_lineup(connection, team_norm="mexico", payload=first_lineup)
    queue_official_lineup_notifications(
        connection,
        match_id="match-1",
        kickoff_at="2026-06-13T13:00:00-06:00",
        lineups_payload={"response": [first_lineup]},
        now=now,
        settings=settings,
    )
    first_result = dispatch_notifications(
        connection,
        now=now,
        settings=settings,
        sessions={"ntfy": FakeSession(FakeResponse(200))},
    )

    second_lineup = _official_lineup_payload(
        "Mexico",
        starters=["Mexico Player 1"] + [f"Mexico XI {index}" for index in range(2, 12)],
    )
    _store_official_lineup(connection, team_norm="mexico", payload=second_lineup)
    queue_official_lineup_notifications(
        connection,
        match_id="match-1",
        kickoff_at="2026-06-13T13:00:00-06:00",
        lineups_payload={"response": [second_lineup]},
        now=datetime(2026, 6, 13, 12, 41, tzinfo=TZ),
        settings=settings,
    )
    second_result = dispatch_notifications(
        connection,
        now=datetime(2026, 6, 13, 12, 41, tzinfo=TZ),
        settings=settings,
        sessions={"ntfy": FakeSession(FakeResponse(200))},
    )

    rows = connection.execute(
        """
        SELECT status, lineup_hash
        FROM notification_deliveries
        WHERE notification_type = 'official_lineup'
        ORDER BY id
        """
    ).fetchall()
    assert first_result["sent"] == 1
    assert second_result["sent"] == 1
    assert [row["status"] for row in rows] == ["sent", "sent"]
    assert rows[0]["lineup_hash"] != rows[1]["lineup_hash"]


def test_fetch_today_data_stores_and_dispatches_official_lineups(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "refresh.sqlite"
    settings = _settings(
        db_path=db_path,
        outputs_dir=tmp_path / "outputs",
        bundles_dir=tmp_path / "outputs" / "bundles",
        predictions_dir=tmp_path / "outputs" / "predictions",
        logs_dir=tmp_path / "outputs" / "logs",
        data_dir=tmp_path / "data",
        raw_dir=tmp_path / "data" / "raw",
        processed_dir=tmp_path / "data" / "processed",
        db_dir=tmp_path / "data" / "db",
        public_dir=tmp_path / "data" / "public",
        model_artifacts_dir=tmp_path / "data" / "processed" / "model_artifacts",
    )
    _seed_match(get_connection(db_path))
    _seed_prediction(get_connection(db_path))

    fixture = {
        "fixture": {
            "id": 123,
            "date": "2026-06-13T19:00:00+00:00",
            "status": {"short": "NS"},
        },
        "teams": {
            "home": {"name": "Mexico"},
            "away": {"name": "Canada"},
        },
        "league": {"name": "World Cup"},
        "goals": {"home": None, "away": None},
    }
    lineups_payload = {
        "response": [
            _official_lineup_payload("Mexico"),
            _official_lineup_payload("Canada"),
        ]
    }
    queue_now = datetime(2026, 6, 13, 12, 40, tzinfo=TZ)

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return queue_now.replace(tzinfo=None)
            return queue_now.astimezone(tz)

    class FakeAPIFootballClient:
        def __init__(self, settings=None, dry_run=False, force_refresh=False, session=None):
            self.settings = settings
            self.connection = get_connection(settings.db_path)

        def get_fixtures(self, **params):
            if params.get("date") == "2026-06-13":
                return {"response": [fixture]}
            return {"response": []}

        def get_fixture_lineups(self, fixture_id):
            assert str(fixture_id) == "123"
            return lineups_payload

    monkeypatch.setattr(historical_loader, "APIFootballClient", FakeAPIFootballClient)
    monkeypatch.setattr("quiniela.output_manager.rebuild_outputs", lambda **kwargs: None)
    monkeypatch.setattr(historical_loader, "get_settings", lambda: settings)
    monkeypatch.setattr(notifications_module, "get_settings", lambda: settings)
    monkeypatch.setattr(notifications_module, "datetime", FrozenDateTime)

    refresh_result = historical_loader.fetch_today_data("2026-06-13", fetch_mode="lineups")

    rows = get_connection(db_path).execute(
        """
        SELECT fixture_id, team_norm, source_json
        FROM historical_lineups
        ORDER BY team_norm
        """
    ).fetchall()
    deliveries = get_connection(db_path).execute(
        """
        SELECT channel, team_norm, notification_type, status, payload_json
        FROM notification_deliveries
        ORDER BY id
        """
    ).fetchall()
    assert refresh_result["fixtures"] == 1
    assert refresh_result["lineups"] == 2
    assert refresh_result["official_lineup_notifications"] == 4
    assert [(row["fixture_id"], row["team_norm"]) for row in rows] == [
        ("123", "canada"),
        ("123", "mexico"),
    ]
    assert len(deliveries) == 4
    assert {(row["notification_type"], row["status"]) for row in deliveries} == {
        ("official_lineup", "pending"),
    }

    ntfy = FakeSession(FakeResponse(200), FakeResponse(200))
    discord = FakeSession(FakeResponse(204), FakeResponse(204))
    dispatch_result = dispatch_notifications(
        get_connection(db_path),
        now=queue_now,
        settings=settings,
        sessions={"ntfy": ntfy, "discord": discord},
    )

    sent_rows = get_connection(db_path).execute(
        """
        SELECT channel, team_norm, status, payload_json
        FROM notification_deliveries
        ORDER BY id
        """
    ).fetchall()
    assert dispatch_result["sent"] == 4
    assert [call["headers"]["Title"] for call in ntfy.calls] == [
        "Alineacion oficial: Mexico",
        "Alineacion oficial: Canada",
    ]
    assert [call["json"]["embeds"][0]["title"] for call in discord.calls] == [
        "Alineacion oficial: Mexico",
        "Alineacion oficial: Canada",
    ]
    assert all(row["status"] == "sent" for row in sent_rows)
    assert "XI oficial Mexico" in sent_rows[0]["payload_json"]
    assert "Mini pronostico" in sent_rows[0]["payload_json"]
    assert "Rival" in discord.calls[0]["json"]["embeds"][0]["fields"][0]["name"]


def test_manual_test_messages_do_not_require_global_switch(tmp_path: Path) -> None:
    settings = _settings(notifications_enabled=False)
    ntfy = FakeSession(FakeResponse(200))
    discord = FakeSession(FakeResponse(204))

    result = send_test_notifications(
        channel="all",
        settings=settings,
        sessions={"ntfy": ntfy, "discord": discord},
    )

    assert result["results"]["ntfy"]["success"] is True
    assert result["results"]["discord"]["success"] is True
