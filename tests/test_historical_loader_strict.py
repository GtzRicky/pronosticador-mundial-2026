from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from quiniela.config import get_settings
from quiniela.db import get_connection
from quiniela.historical_loader import (
    RetrievalValidationError,
    fetch_today_data,
    resolve_team_id,
)
from quiniela import historical_loader
from quiniela import output_manager


def _seed_match(connection) -> None:
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
                '2026-06-13', '13:00', '2026-06-13T13:00:00-06:00',
                'Mexico', 'Canada', 'mexico', 'canada',
                'A', 'Test Stadium', 'Group', 'NS', NULL
            )
            """
        )


def test_fetch_today_data_fails_on_unknown_live_team_name(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = get_connection(tmp_path / "unknown-live-team.sqlite")
    _seed_match(connection)
    settings = replace(get_settings(), db_path=tmp_path / "unknown-live-team.sqlite")

    class FakeClient:
        def __init__(self, *args, **kwargs):
            self.connection = connection

        def get_fixtures(self, **params):
            return {
                "response": [
                    {
                        "fixture": {
                            "id": 123,
                            "date": "2026-06-13T19:00:00+00:00",
                            "status": {"short": "NS"},
                        },
                        "teams": {
                            "home": {"name": "Atlantis"},
                            "away": {"name": "Canada"},
                        },
                        "goals": {"home": None, "away": None},
                        "league": {"name": "World Cup"},
                    }
                ]
            }

    monkeypatch.setattr(historical_loader, "APIFootballClient", FakeClient)
    monkeypatch.setattr(historical_loader, "get_settings", lambda: settings)
    monkeypatch.setattr(output_manager, "rebuild_outputs", lambda **kwargs: {})

    with pytest.raises(RetrievalValidationError) as exc_info:
        fetch_today_data("2026-06-13", fetch_mode="post_status")

    assert exc_info.value.issues[0]["reason"] == "unknown_live_team_name"


def test_fetch_today_data_fails_on_fixture_schedule_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = get_connection(tmp_path / "fixture-mismatch.sqlite")
    _seed_match(connection)
    settings = replace(get_settings(), db_path=tmp_path / "fixture-mismatch.sqlite")

    class FakeClient:
        def __init__(self, *args, **kwargs):
            self.connection = connection

        def get_fixtures(self, **params):
            return {
                "response": [
                    {
                        "fixture": {
                            "id": 456,
                            "date": "2026-06-13T19:00:00+00:00",
                            "status": {"short": "NS"},
                        },
                        "teams": {
                            "home": {"name": "Mexico"},
                            "away": {"name": "Panama"},
                        },
                        "goals": {"home": None, "away": None},
                        "league": {"name": "World Cup"},
                    }
                ]
            }

    monkeypatch.setattr(historical_loader, "APIFootballClient", FakeClient)
    monkeypatch.setattr(historical_loader, "get_settings", lambda: settings)
    monkeypatch.setattr(output_manager, "rebuild_outputs", lambda **kwargs: {})

    with pytest.raises(RetrievalValidationError) as exc_info:
        fetch_today_data("2026-06-13", fetch_mode="post_status")

    assert exc_info.value.issues[0]["reason"] == "fixture_not_in_local_schedule"


def test_resolve_team_id_requires_exact_national_candidate(tmp_path: Path) -> None:
    connection = get_connection(tmp_path / "resolve-team-id.sqlite")

    class FakeClient:
        def __init__(self):
            self.connection = connection

        def search_teams(self, team_name: str):
            assert team_name in {"Mexico", "MÃ©xico", "M?xico", "México"}
            return {
                "response": [
                    {
                        "team": {
                            "id": 999,
                            "name": "Mexico",
                            "national": False,
                        }
                    }
                ]
            }

    assert resolve_team_id(FakeClient(), "Mexico") is None
