from __future__ import annotations

from pathlib import Path
import json
import sqlite3
from typing import Any, Iterable

import pandas as pd

from quiniela.cache import canonical_json, hash_params
from quiniela.config import get_settings
from quiniela.name_maps import normalize_team_name


SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS teams (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        team_name TEXT NOT NULL,
        team_norm TEXT NOT NULL UNIQUE,
        api_team_id INTEGER,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS players (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        team TEXT NOT NULL,
        team_norm TEXT NOT NULL,
        player TEXT NOT NULL,
        player_norm TEXT NOT NULL,
        position_group TEXT NOT NULL,
        club TEXT,
        coach TEXT,
        is_active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(team_norm, player_norm)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS matches (
        match_id TEXT PRIMARY KEY,
        date_et TEXT NOT NULL,
        time_et TEXT NOT NULL,
        datetime_et TEXT NOT NULL,
        date_cdmx TEXT NOT NULL,
        time_cdmx TEXT NOT NULL,
        datetime_cdmx TEXT NOT NULL,
        home_team TEXT NOT NULL,
        away_team TEXT NOT NULL,
        home_team_norm TEXT NOT NULL,
        away_team_norm TEXT NOT NULL,
        group_name TEXT NOT NULL,
        stadium TEXT NOT NULL,
        stage TEXT NOT NULL,
        status TEXT NOT NULL,
        api_fixture_id INTEGER
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS api_cache (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        endpoint TEXT NOT NULL,
        params_hash TEXT NOT NULL,
        params_json TEXT NOT NULL,
        response_json TEXT NOT NULL,
        status_code INTEGER,
        cached_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(endpoint, params_hash)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS api_usage (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        request_date TEXT NOT NULL,
        endpoint TEXT NOT NULL,
        params_hash TEXT NOT NULL,
        cache_hit INTEGER NOT NULL DEFAULT 0,
        status_code INTEGER,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS historical_matches (
        fixture_id TEXT PRIMARY KEY,
        match_date TEXT,
        home_team TEXT,
        away_team TEXT,
        home_team_norm TEXT,
        away_team_norm TEXT,
        home_goals INTEGER,
        away_goals INTEGER,
        status TEXT,
        league_name TEXT,
        source_json TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS historical_lineups (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fixture_id TEXT NOT NULL,
        team_norm TEXT NOT NULL,
        source_json TEXT NOT NULL,
        fetched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(fixture_id, team_norm)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS historical_team_stats (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fixture_id TEXT NOT NULL,
        team_norm TEXT NOT NULL,
        source_json TEXT NOT NULL,
        fetched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(fixture_id, team_norm)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS historical_player_stats (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fixture_id TEXT NOT NULL,
        team_norm TEXT,
        player_norm TEXT,
        source_json TEXT NOT NULL,
        fetched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS odds_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fixture_id TEXT NOT NULL,
        bookmaker TEXT,
        market TEXT,
        source_json TEXT NOT NULL,
        fetched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS predictions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        match_id TEXT,
        datetime_cdmx TEXT,
        group_name TEXT,
        home_team TEXT NOT NULL,
        away_team TEXT NOT NULL,
        predicted_score TEXT NOT NULL,
        probability REAL NOT NULL,
        model_version TEXT NOT NULL,
        source_json TEXT,
        generated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS actual_results (
        match_id TEXT PRIMARY KEY,
        home_goals INTEGER,
        away_goals INTEGER,
        result_json TEXT,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
]


def get_connection(db_path: Path | None = None) -> sqlite3.Connection:
    settings = get_settings()
    path = db_path or settings.db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def create_schema(connection: sqlite3.Connection) -> None:
    with connection:
        for statement in SCHEMA_STATEMENTS:
            connection.execute(statement)


def _executemany(connection: sqlite3.Connection, query: str, rows: Iterable[tuple[Any, ...]]) -> None:
    rows = list(rows)
    if not rows:
        return
    with connection:
        connection.executemany(query, rows)


def load_matches(connection: sqlite3.Connection, matches_df: pd.DataFrame) -> int:
    rows = [
        (
            row["match_id"],
            row["date_et"],
            row["time_et"],
            row["datetime_et"],
            row["date_cdmx"],
            row["time_cdmx"],
            row["datetime_cdmx"],
            row["home_team"],
            row["away_team"],
            row["home_team_norm"],
            row["away_team_norm"],
            row["group"],
            row["stadium"],
            row["stage"],
            row["status"],
        )
        for row in matches_df.to_dict(orient="records")
    ]
    _executemany(
        connection,
        """
        INSERT OR REPLACE INTO matches (
            match_id, date_et, time_et, datetime_et, date_cdmx, time_cdmx, datetime_cdmx,
            home_team, away_team, home_team_norm, away_team_norm, group_name, stadium, stage, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    return len(rows)


def load_players(connection: sqlite3.Connection, rosters_df: pd.DataFrame) -> int:
    rows = [
        (
            row["team"],
            row["team_norm"],
            row["player"],
            row["player_norm"],
            row["position_group"],
            row.get("club"),
            row.get("coach"),
            int(row.get("is_active", 1)),
        )
        for row in rosters_df.to_dict(orient="records")
    ]
    _executemany(
        connection,
        """
        INSERT OR REPLACE INTO players (
            team, team_norm, player, player_norm, position_group, club, coach, is_active
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    return len(rows)


def load_teams(connection: sqlite3.Connection, matches_df: pd.DataFrame, rosters_df: pd.DataFrame) -> int:
    teams: dict[str, str] = {}

    for row in matches_df.to_dict(orient="records"):
        teams.setdefault(row["home_team_norm"], row["home_team"])
        teams.setdefault(row["away_team_norm"], row["away_team"])

    for row in rosters_df.to_dict(orient="records"):
        teams.setdefault(row["team_norm"], row["team"])

    rows = [(name, norm) for norm, name in sorted(teams.items())]
    _executemany(
        connection,
        "INSERT OR IGNORE INTO teams (team_name, team_norm) VALUES (?, ?)",
        rows,
    )
    return len(rows)


def seed_from_processed(
    connection: sqlite3.Connection,
    calendar_path: Path | None = None,
    rosters_path: Path | None = None,
) -> dict[str, int]:
    settings = get_settings()
    calendar_path = calendar_path or settings.processed_dir / "calendar.csv"
    rosters_path = rosters_path or settings.processed_dir / "rosters.csv"
    matches_df = pd.read_csv(calendar_path)
    rosters_df = pd.read_csv(rosters_path)
    with connection:
        connection.execute("DELETE FROM players")
        connection.execute("DELETE FROM matches")
        connection.execute("DELETE FROM teams")
    return {
        "teams": load_teams(connection, matches_df, rosters_df),
        "players": load_players(connection, rosters_df),
        "matches": load_matches(connection, matches_df),
    }


def record_api_usage(
    connection: sqlite3.Connection,
    endpoint: str,
    params: dict[str, Any] | None,
    cache_hit: bool,
    status_code: int | None,
    request_date: str | None = None,
) -> None:
    params_hash = hash_params(params)
    request_date = request_date or pd.Timestamp.utcnow().date().isoformat()
    with connection:
        connection.execute(
            """
            INSERT INTO api_usage (request_date, endpoint, params_hash, cache_hit, status_code)
            VALUES (?, ?, ?, ?, ?)
            """,
            (request_date, endpoint, params_hash, int(cache_hit), status_code),
        )


def count_api_requests_today(connection: sqlite3.Connection, request_date: str | None = None) -> int:
    request_date = request_date or pd.Timestamp.utcnow().date().isoformat()
    row = connection.execute(
        "SELECT COUNT(*) AS total FROM api_usage WHERE request_date = ? AND cache_hit = 0",
        (request_date,),
    ).fetchone()
    return int(row["total"])


def get_cached_response(
    connection: sqlite3.Connection, endpoint: str, params: dict[str, Any] | None
) -> dict[str, Any] | None:
    params_hash = hash_params(params)
    row = connection.execute(
        """
        SELECT response_json
        FROM api_cache
        WHERE endpoint = ? AND params_hash = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (endpoint, params_hash),
    ).fetchone()
    if row is None:
        return None
    return json.loads(row["response_json"])


def set_cached_response(
    connection: sqlite3.Connection,
    endpoint: str,
    params: dict[str, Any] | None,
    response_payload: dict[str, Any],
    status_code: int | None = None,
) -> None:
    params_hash = hash_params(params)
    with connection:
        connection.execute(
            """
            INSERT INTO api_cache (endpoint, params_hash, params_json, response_json, status_code)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(endpoint, params_hash) DO UPDATE SET
                params_json = excluded.params_json,
                response_json = excluded.response_json,
                status_code = excluded.status_code,
                cached_at = CURRENT_TIMESTAMP
            """,
            (
                endpoint,
                params_hash,
                canonical_json(params),
                json.dumps(response_payload, ensure_ascii=False),
                status_code,
            ),
        )


def delete_cached_response(
    connection: sqlite3.Connection,
    endpoint: str,
    params: dict[str, Any] | None = None,
) -> int:
    params_hash = hash_params(params)
    with connection:
        cursor = connection.execute(
            "DELETE FROM api_cache WHERE endpoint = ? AND params_hash = ?",
            (endpoint, params_hash),
        )
    return int(cursor.rowcount)


def delete_cached_endpoint(connection: sqlite3.Connection, endpoint: str) -> int:
    with connection:
        cursor = connection.execute("DELETE FROM api_cache WHERE endpoint = ?", (endpoint,))
    return int(cursor.rowcount)


def upsert_team_api_id(connection: sqlite3.Connection, team_name: str, api_team_id: int) -> None:
    team_norm = normalize_team_name(team_name)
    with connection:
        connection.execute(
            """
            INSERT INTO teams (team_name, team_norm, api_team_id)
            VALUES (?, ?, ?)
            ON CONFLICT(team_norm) DO UPDATE SET
                team_name = excluded.team_name,
                api_team_id = excluded.api_team_id,
                updated_at = CURRENT_TIMESTAMP
            """,
            (team_name, team_norm, api_team_id),
        )


def get_team_api_id(connection: sqlite3.Connection, team_name_or_norm: str) -> int | None:
    team_norm = normalize_team_name(team_name_or_norm)
    row = connection.execute(
        "SELECT api_team_id FROM teams WHERE team_norm = ?",
        (team_norm,),
    ).fetchone()
    if row is None or row["api_team_id"] is None:
        return None
    return int(row["api_team_id"])


def store_historical_match(connection: sqlite3.Connection, payload: dict[str, Any]) -> None:
    fixture = payload.get("fixture", {})
    teams = payload.get("teams", {})
    goals = payload.get("goals", {})
    league = payload.get("league", {})
    fixture_id = str(fixture.get("id"))
    if not fixture_id or fixture_id == "None":
        return

    home_team = teams.get("home", {}).get("name")
    away_team = teams.get("away", {}).get("name")
    with connection:
        connection.execute(
            """
            INSERT INTO historical_matches (
                fixture_id, match_date, home_team, away_team, home_team_norm, away_team_norm,
                home_goals, away_goals, status, league_name, source_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(fixture_id) DO UPDATE SET
                match_date = excluded.match_date,
                home_team = excluded.home_team,
                away_team = excluded.away_team,
                home_team_norm = excluded.home_team_norm,
                away_team_norm = excluded.away_team_norm,
                home_goals = excluded.home_goals,
                away_goals = excluded.away_goals,
                status = excluded.status,
                league_name = excluded.league_name,
                source_json = excluded.source_json,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                fixture_id,
                fixture.get("date"),
                home_team,
                away_team,
                normalize_team_name(home_team or ""),
                normalize_team_name(away_team or ""),
                goals.get("home"),
                goals.get("away"),
                fixture.get("status", {}).get("short"),
                league.get("name"),
                json.dumps(payload, ensure_ascii=False),
            ),
        )


def store_json_row(
    connection: sqlite3.Connection,
    table_name: str,
    fixture_id: str,
    team_norm: str | None,
    payload: dict[str, Any],
    bookmaker: str | None = None,
    market: str | None = None,
    player_norm: str | None = None,
) -> None:
    serialized = json.dumps(payload, ensure_ascii=False)
    with connection:
        if table_name == "historical_lineups":
            connection.execute(
                """
                INSERT INTO historical_lineups (fixture_id, team_norm, source_json)
                VALUES (?, ?, ?)
                ON CONFLICT(fixture_id, team_norm) DO UPDATE SET
                    source_json = excluded.source_json,
                    fetched_at = CURRENT_TIMESTAMP
                """,
                (fixture_id, team_norm, serialized),
            )
        elif table_name == "historical_team_stats":
            connection.execute(
                """
                INSERT INTO historical_team_stats (fixture_id, team_norm, source_json)
                VALUES (?, ?, ?)
                ON CONFLICT(fixture_id, team_norm) DO UPDATE SET
                    source_json = excluded.source_json,
                    fetched_at = CURRENT_TIMESTAMP
                """,
                (fixture_id, team_norm, serialized),
            )
        elif table_name == "historical_player_stats":
            connection.execute(
                """
                INSERT INTO historical_player_stats (fixture_id, team_norm, player_norm, source_json)
                VALUES (?, ?, ?, ?)
                """,
                (fixture_id, team_norm, player_norm, serialized),
            )
        elif table_name == "odds_snapshots":
            connection.execute(
                """
                INSERT INTO odds_snapshots (fixture_id, bookmaker, market, source_json)
                VALUES (?, ?, ?, ?)
                """,
                (fixture_id, bookmaker, market, serialized),
            )


def insert_prediction_rows(connection: sqlite3.Connection, predictions_df: pd.DataFrame) -> int:
    rows = [
        (
            row.get("match_id"),
            row.get("datetime_cdmx"),
            row.get("group"),
            row.get("home_team"),
            row.get("away_team"),
            row.get("predicted_score"),
            float(row.get("probability", 0.0)),
            row.get("model_version"),
            json.dumps(row, ensure_ascii=False),
        )
        for row in predictions_df.to_dict(orient="records")
    ]
    _executemany(
        connection,
        """
        INSERT INTO predictions (
            match_id, datetime_cdmx, group_name, home_team, away_team,
            predicted_score, probability, model_version, source_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    return len(rows)


def fetch_dataframe(connection: sqlite3.Connection, query: str, params: Iterable[Any] | None = None) -> pd.DataFrame:
    return pd.read_sql_query(query, connection, params=params or ())
