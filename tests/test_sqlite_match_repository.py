from __future__ import annotations

from pathlib import Path

import pandas as pd

from quiniela.adapters.outbound.sqlite.match_repository import SQLiteMatchRepository
from quiniela.db import get_connection, load_matches
from quiniela.domain import MatchId, TeamId


def _matches_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "match_id": "match-2",
                "date_et": "2026-06-11",
                "time_et": "21:00",
                "datetime_et": "2026-06-11T21:00:00-04:00",
                "date_cdmx": "2026-06-11",
                "time_cdmx": "19:00",
                "datetime_cdmx": "2026-06-11T19:00:00-06:00",
                "home_team": "Canada",
                "away_team": "Mexico",
                "group": "A",
                "stadium": "Azteca",
                "stage": "group",
                "status": "NS",
            },
            {
                "match_id": "match-1",
                "date_et": "2026-06-11",
                "time_et": "18:00",
                "datetime_et": "2026-06-11T18:00:00-04:00",
                "date_cdmx": "2026-06-11",
                "time_cdmx": "16:00",
                "datetime_cdmx": "2026-06-11T16:00:00-06:00",
                "home_team": "Mexico",
                "away_team": "Canada",
                "group": "A",
                "stadium": "Azteca",
                "stage": "group",
                "status": "NS",
            },
        ]
    )


def test_sqlite_match_repository_get_by_id_maps_domain_record(tmp_path: Path) -> None:
    connection = get_connection(tmp_path / "matches.sqlite")
    load_matches(connection, _matches_frame())

    repository = SQLiteMatchRepository(connection)
    match = repository.get_by_id(MatchId("match-1"))

    assert match is not None
    assert match.match_id == MatchId("match-1")
    assert match.home_team_id == TeamId("mexico")
    assert match.away_team_id == TeamId("canada")
    assert match.home_team == "Mexico"
    assert match.group_name == "A"
    assert match.kickoff_at.isoformat() == "2026-06-11T16:00:00-06:00"


def test_sqlite_match_repository_list_by_date_preserves_legacy_order(tmp_path: Path) -> None:
    connection = get_connection(tmp_path / "matches.sqlite")
    load_matches(connection, _matches_frame())

    repository = SQLiteMatchRepository(connection)
    matches = repository.list_by_date("2026-06-11")

    assert [str(match.match_id) for match in matches] == ["match-1", "match-2"]


def test_sqlite_match_repository_returns_none_for_missing_match(tmp_path: Path) -> None:
    connection = get_connection(tmp_path / "matches.sqlite")
    repository = SQLiteMatchRepository(connection)

    assert repository.get_by_id(MatchId("missing")) is None
