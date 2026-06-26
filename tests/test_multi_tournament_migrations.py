from __future__ import annotations

from pathlib import Path
import sqlite3

import pandas as pd

from quiniela.db import (
    DEFAULT_COMPETITION_ID,
    DEFAULT_SEASON_ID,
    create_schema,
    get_connection,
    load_teams,
)


def _table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _columns(connection: sqlite3.Connection, table_name: str) -> set[str]:
    return {
        str(row["name"])
        for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    }


def test_multi_tournament_tables_and_default_rows_are_created(tmp_path: Path) -> None:
    connection = get_connection(tmp_path / "multi-tournament.sqlite")

    assert _table_exists(connection, "competitions")
    assert _table_exists(connection, "seasons")
    assert _table_exists(connection, "competition_participants")

    competition = connection.execute(
        "SELECT competition_id, name, competition_type FROM competitions WHERE competition_id = ?",
        (DEFAULT_COMPETITION_ID,),
    ).fetchone()
    season = connection.execute(
        "SELECT season_id, competition_id, name FROM seasons WHERE season_id = ?",
        (DEFAULT_SEASON_ID,),
    ).fetchone()

    assert dict(competition) == {
        "competition_id": "fifa_world_cup",
        "name": "FIFA World Cup",
        "competition_type": "national_teams",
    }
    assert dict(season) == {
        "season_id": "world_cup_2026",
        "competition_id": "fifa_world_cup",
        "name": "FIFA World Cup 2026",
    }


def test_operational_tables_receive_default_scope_columns(tmp_path: Path) -> None:
    connection = get_connection(tmp_path / "scoped.sqlite")
    scoped_tables = {
        "players",
        "matches",
        "odds_market_snapshots",
        "odds_market_consensus",
        "predictions",
        "notification_deliveries",
        "pre_match_snapshots",
        "model_training_runs",
        "model_releases",
    }

    for table_name in scoped_tables:
        assert {"competition_id", "season_id"}.issubset(_columns(connection, table_name))


def test_migration_backfills_existing_rows_without_losing_data(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.sqlite"
    legacy_connection = sqlite3.connect(db_path)
    with legacy_connection:
        legacy_connection.execute(
            """
            CREATE TABLE teams (
                team_name TEXT NOT NULL,
                team_norm TEXT NOT NULL UNIQUE
            )
            """
        )
        legacy_connection.execute(
            """
            CREATE TABLE matches (
                match_id TEXT PRIMARY KEY,
                home_team TEXT NOT NULL,
                away_team TEXT NOT NULL
            )
            """
        )
        legacy_connection.execute("INSERT INTO teams VALUES ('Mexico', 'mexico')")
        legacy_connection.execute("INSERT INTO matches VALUES ('match-1', 'Mexico', 'Canada')")
    legacy_connection.close()

    connection = get_connection(db_path)
    match = connection.execute(
        "SELECT match_id, competition_id, season_id FROM matches WHERE match_id = 'match-1'"
    ).fetchone()
    participant = connection.execute(
        """
        SELECT participant_id, competition_id, season_id, team_norm, display_name
        FROM competition_participants
        WHERE team_norm = 'mexico'
        """
    ).fetchone()

    assert dict(match) == {
        "match_id": "match-1",
        "competition_id": DEFAULT_COMPETITION_ID,
        "season_id": DEFAULT_SEASON_ID,
    }
    assert dict(participant) == {
        "participant_id": "world_cup_2026:mexico",
        "competition_id": DEFAULT_COMPETITION_ID,
        "season_id": DEFAULT_SEASON_ID,
        "team_norm": "mexico",
        "display_name": "Mexico",
    }


def test_schema_migration_is_idempotent(tmp_path: Path) -> None:
    connection = get_connection(tmp_path / "idempotent.sqlite")

    create_schema(connection)
    create_schema(connection)

    assert connection.execute("SELECT COUNT(*) FROM competitions").fetchone()[0] == 1
    assert connection.execute("SELECT COUNT(*) FROM seasons").fetchone()[0] == 1


def test_load_teams_syncs_default_competition_participants(tmp_path: Path) -> None:
    connection = get_connection(tmp_path / "participants.sqlite")
    matches_df = pd.DataFrame(
        [
            {
                "home_team": "Mexico",
                "away_team": "Canada",
            }
        ]
    )
    rosters_df = pd.DataFrame(
        [
            {
                "team": "Qatar",
            }
        ]
    )

    load_teams(connection, matches_df, rosters_df)

    participants = connection.execute(
        """
        SELECT team_norm, season_id
        FROM competition_participants
        ORDER BY team_norm
        """
    ).fetchall()

    assert [(row["team_norm"], row["season_id"]) for row in participants] == [
        ("canada", DEFAULT_SEASON_ID),
        ("mexico", DEFAULT_SEASON_ID),
        ("qatar", DEFAULT_SEASON_ID),
    ]
