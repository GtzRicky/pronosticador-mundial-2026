from __future__ import annotations

from pathlib import Path
import json
import sqlite3
from typing import Any, Iterable

import pandas as pd

from quiniela.cache import canonical_json, hash_params
from quiniela.config import get_settings
from quiniela.name_maps import normalize_team_name, normalize_text


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
        api_player_id INTEGER,
        api_player_name TEXT,
        api_position TEXT,
        height_cm REAL,
        nationality TEXT,
        last_resolved_at TEXT,
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
    CREATE TABLE IF NOT EXISTS lineup_estimates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fixture_id TEXT NOT NULL,
        match_id TEXT NOT NULL,
        team_norm TEXT NOT NULL,
        window_label TEXT NOT NULL,
        confidence REAL NOT NULL,
        query_json TEXT NOT NULL,
        sources_json TEXT NOT NULL,
        source_json TEXT NOT NULL,
        generated_at TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(fixture_id, team_norm, window_label)
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
    CREATE TABLE IF NOT EXISTS fixture_player_stats (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fixture_id TEXT NOT NULL,
        api_player_id INTEGER NOT NULL,
        team_norm TEXT NOT NULL,
        player_name TEXT NOT NULL,
        player_norm TEXT,
        minutes INTEGER,
        number INTEGER,
        position TEXT,
        rating REAL,
        shots_total REAL,
        shots_on REAL,
        goals_total REAL,
        goals_conceded REAL,
        goals_assists REAL,
        goals_saves REAL,
        passes_total REAL,
        passes_key REAL,
        passes_accuracy REAL,
        tackles_total REAL,
        tackles_blocks REAL,
        tackles_interceptions REAL,
        duels_total REAL,
        duels_won REAL,
        dribbles_attempts REAL,
        dribbles_success REAL,
        dribbles_past REAL,
        fouls_drawn REAL,
        fouls_committed REAL,
        cards_yellow REAL,
        cards_red REAL,
        penalty_won REAL,
        penalty_commited REAL,
        penalty_scored REAL,
        penalty_missed REAL,
        penalty_saved REAL,
        source_json TEXT NOT NULL,
        fetched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(fixture_id, api_player_id, team_norm)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS player_season_stats (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        api_player_id INTEGER NOT NULL,
        team_norm TEXT NOT NULL,
        player_name TEXT NOT NULL,
        player_norm TEXT,
        season INTEGER NOT NULL,
        league_id INTEGER,
        league_name TEXT,
        appearences REAL,
        lineups REAL,
        minutes REAL,
        rating REAL,
        shots_total REAL,
        shots_on REAL,
        goals_total REAL,
        goals_assists REAL,
        passes_total REAL,
        passes_key REAL,
        tackles_total REAL,
        tackles_interceptions REAL,
        duels_total REAL,
        duels_won REAL,
        dribbles_attempts REAL,
        dribbles_success REAL,
        fouls_drawn REAL,
        fouls_committed REAL,
        cards_yellow REAL,
        cards_red REAL,
        penalty_won REAL,
        penalty_commited REAL,
        penalty_scored REAL,
        penalty_missed REAL,
        penalty_saved REAL,
        height_cm REAL,
        nationality TEXT,
        position TEXT,
        source_json TEXT NOT NULL,
        fetched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(api_player_id, team_norm, season, league_id)
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
        hybrid_predicted_score TEXT,
        hybrid_probability REAL,
        home_win_probability REAL,
        draw_probability REAL,
        away_win_probability REAL,
        outcome_model_version TEXT,
        data_freshness_at TEXT,
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
    """
    CREATE TABLE IF NOT EXISTS prediction_player_impacts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        prediction_id INTEGER,
        match_id TEXT NOT NULL,
        team_norm TEXT NOT NULL,
        api_player_id INTEGER,
        player_name TEXT NOT NULL,
        role_bucket TEXT NOT NULL,
        attack_impact REAL NOT NULL DEFAULT 0,
        defense_impact REAL NOT NULL DEFAULT 0,
        discipline_impact REAL NOT NULL DEFAULT 0,
        availability_impact REAL NOT NULL DEFAULT 0,
        net_impact REAL NOT NULL DEFAULT 0,
        source_json TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(prediction_id) REFERENCES predictions(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS prediction_evaluations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        prediction_id INTEGER NOT NULL UNIQUE,
        match_id TEXT NOT NULL,
        is_canonical INTEGER NOT NULL DEFAULT 0,
        actual_home_goals INTEGER NOT NULL,
        actual_away_goals INTEGER NOT NULL,
        predicted_home_goals INTEGER,
        predicted_away_goals INTEGER,
        hybrid_home_goals INTEGER,
        hybrid_away_goals INTEGER,
        goal_mae REAL,
        poisson_deviance REAL,
        outcome_correct INTEGER,
        log_loss REAL,
        brier_score REAL,
        calibration_error REAL,
        metrics_json TEXT NOT NULL,
        evaluated_at TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(prediction_id) REFERENCES predictions(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS automation_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_key TEXT NOT NULL UNIQUE,
        action TEXT NOT NULL,
        match_id TEXT,
        scheduled_for TEXT NOT NULL,
        status TEXT NOT NULL,
        details_json TEXT,
        started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        finished_at TEXT,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS notification_deliveries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        match_id TEXT NOT NULL,
        kickoff_at TEXT NOT NULL,
        window_label TEXT NOT NULL,
        channel TEXT NOT NULL,
        notification_type TEXT NOT NULL DEFAULT 'prediction_window',
        team_norm TEXT,
        lineup_hash TEXT,
        prediction_id INTEGER,
        scheduled_for TEXT NOT NULL,
        payload_json TEXT,
        status TEXT NOT NULL DEFAULT 'pending',
        attempt_count INTEGER NOT NULL DEFAULT 0,
        next_attempt_at TEXT NOT NULL,
        last_attempt_at TEXT,
        sent_at TEXT,
        last_http_status INTEGER,
        error_code TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(match_id, kickoff_at, window_label, channel),
        FOREIGN KEY(prediction_id) REFERENCES predictions(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS pre_match_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        match_id TEXT NOT NULL,
        fixture_id TEXT,
        window_label TEXT NOT NULL,
        source_kind TEXT NOT NULL,
        kickoff_at TEXT NOT NULL,
        captured_at TEXT NOT NULL,
        features_json TEXT NOT NULL,
        prediction_json TEXT NOT NULL,
        home_lineup_source TEXT,
        away_lineup_source TEXT,
        model_version TEXT,
        outcome_model_version TEXT,
        data_hash TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(match_id, window_label, source_kind)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS pre_match_player_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        snapshot_id INTEGER NOT NULL,
        team_norm TEXT NOT NULL,
        api_player_id INTEGER,
        player_name TEXT NOT NULL,
        player_norm TEXT,
        lineup_role TEXT NOT NULL,
        role_bucket TEXT NOT NULL,
        attack_impact REAL NOT NULL DEFAULT 0,
        defense_impact REAL NOT NULL DEFAULT 0,
        discipline_impact REAL NOT NULL DEFAULT 0,
        availability_impact REAL NOT NULL DEFAULT 0,
        net_impact REAL NOT NULL DEFAULT 0,
        features_json TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(snapshot_id, team_norm, player_name),
        FOREIGN KEY(snapshot_id) REFERENCES pre_match_snapshots(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS player_match_targets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        match_id TEXT NOT NULL,
        fixture_id TEXT NOT NULL,
        snapshot_id INTEGER NOT NULL,
        team_norm TEXT NOT NULL,
        api_player_id INTEGER,
        player_name TEXT NOT NULL,
        player_norm TEXT,
        participated INTEGER NOT NULL,
        minutes REAL NOT NULL DEFAULT 0,
        rating REAL,
        shots_total REAL NOT NULL DEFAULT 0,
        shots_on REAL NOT NULL DEFAULT 0,
        goals_total REAL NOT NULL DEFAULT 0,
        goals_assists REAL NOT NULL DEFAULT 0,
        dribbles_attempts REAL NOT NULL DEFAULT 0,
        dribbles_success REAL NOT NULL DEFAULT 0,
        passes_total REAL NOT NULL DEFAULT 0,
        passes_key REAL NOT NULL DEFAULT 0,
        passes_accuracy REAL,
        tackles_total REAL NOT NULL DEFAULT 0,
        tackles_blocks REAL NOT NULL DEFAULT 0,
        tackles_interceptions REAL NOT NULL DEFAULT 0,
        duels_total REAL NOT NULL DEFAULT 0,
        duels_won REAL NOT NULL DEFAULT 0,
        goals_saves REAL NOT NULL DEFAULT 0,
        goals_conceded REAL NOT NULL DEFAULT 0,
        fouls_committed REAL NOT NULL DEFAULT 0,
        cards_yellow REAL NOT NULL DEFAULT 0,
        cards_red REAL NOT NULL DEFAULT 0,
        penalty_won REAL NOT NULL DEFAULT 0,
        penalty_commited REAL NOT NULL DEFAULT 0,
        penalty_scored REAL NOT NULL DEFAULT 0,
        penalty_missed REAL NOT NULL DEFAULT 0,
        penalty_saved REAL NOT NULL DEFAULT 0,
        target_json TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(snapshot_id, team_norm, player_name),
        FOREIGN KEY(snapshot_id) REFERENCES pre_match_snapshots(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS player_evidence_evaluations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        release_id TEXT NOT NULL,
        snapshot_id INTEGER NOT NULL,
        match_id TEXT NOT NULL,
        team_norm TEXT NOT NULL,
        player_name TEXT NOT NULL,
        metrics_json TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(release_id, snapshot_id, team_norm, player_name)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS player_prediction_evaluations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        snapshot_id INTEGER NOT NULL,
        match_id TEXT NOT NULL,
        team_norm TEXT NOT NULL,
        player_name TEXT NOT NULL,
        source_kind TEXT NOT NULL,
        participated INTEGER NOT NULL,
        minutes REAL NOT NULL DEFAULT 0,
        predicted_attack REAL NOT NULL DEFAULT 0,
        predicted_defense REAL NOT NULL DEFAULT 0,
        predicted_discipline REAL NOT NULL DEFAULT 0,
        predicted_availability REAL NOT NULL DEFAULT 0,
        actual_attack REAL NOT NULL DEFAULT 0,
        actual_creation REAL NOT NULL DEFAULT 0,
        actual_defense REAL NOT NULL DEFAULT 0,
        actual_goalkeeping REAL NOT NULL DEFAULT 0,
        actual_discipline REAL NOT NULL DEFAULT 0,
        metrics_json TEXT NOT NULL,
        evaluated_at TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(snapshot_id, team_norm, player_name),
        FOREIGN KEY(snapshot_id) REFERENCES pre_match_snapshots(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS model_training_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        dataset_hash TEXT NOT NULL UNIQUE,
        status TEXT NOT NULL,
        cutoff_at TEXT NOT NULL,
        match_count INTEGER NOT NULL DEFAULT 0,
        new_match_count INTEGER NOT NULL DEFAULT 0,
        source_counts_json TEXT,
        metrics_json TEXT,
        release_id TEXT,
        reason TEXT,
        started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        finished_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS model_releases (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        release_id TEXT NOT NULL UNIQUE,
        release_path TEXT NOT NULL,
        status TEXT NOT NULL,
        model_version TEXT NOT NULL,
        dataset_hash TEXT NOT NULL,
        cutoff_at TEXT NOT NULL,
        training_matches INTEGER NOT NULL,
        live_matches INTEGER NOT NULL DEFAULT 0,
        metrics_json TEXT NOT NULL,
        preliminary INTEGER NOT NULL DEFAULT 1,
        previous_release_id TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        activated_at TEXT
    )
    """,
]


def get_connection(db_path: Path | None = None) -> sqlite3.Connection:
    settings = get_settings()
    path = db_path or settings.db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    create_schema(connection)
    return connection


def create_schema(connection: sqlite3.Connection) -> None:
    with connection:
        for statement in SCHEMA_STATEMENTS:
            connection.execute(statement)
    _migrate_schema(connection)


def _table_columns(connection: sqlite3.Connection, table_name: str) -> set[str]:
    rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {str(row["name"]) for row in rows}


def _ensure_column(connection: sqlite3.Connection, table_name: str, column_name: str, column_def: str) -> None:
    if column_name in _table_columns(connection, table_name):
        return
    with connection:
        connection.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_def}")


def _migrate_schema(connection: sqlite3.Connection) -> None:
    player_columns = {
        "api_player_id": "api_player_id INTEGER",
        "api_player_name": "api_player_name TEXT",
        "api_position": "api_position TEXT",
        "height_cm": "height_cm REAL",
        "nationality": "nationality TEXT",
        "last_resolved_at": "last_resolved_at TEXT",
    }
    for column_name, column_def in player_columns.items():
        _ensure_column(connection, "players", column_name, column_def)

    prediction_columns = {
        "hybrid_predicted_score": "hybrid_predicted_score TEXT",
        "hybrid_probability": "hybrid_probability REAL",
        "home_win_probability": "home_win_probability REAL",
        "draw_probability": "draw_probability REAL",
        "away_win_probability": "away_win_probability REAL",
        "outcome_model_version": "outcome_model_version TEXT",
        "data_freshness_at": "data_freshness_at TEXT",
        "prediction_context": "prediction_context TEXT NOT NULL DEFAULT 'legacy'",
        "window_label": "window_label TEXT",
        "generated_at_utc": "generated_at_utc TEXT",
        "is_pre_kickoff": "is_pre_kickoff INTEGER",
    }
    for column_name, column_def in prediction_columns.items():
        _ensure_column(connection, "predictions", column_name, column_def)

    notification_columns = {
        "notification_type": "notification_type TEXT NOT NULL DEFAULT 'prediction_window'",
        "team_norm": "team_norm TEXT",
        "lineup_hash": "lineup_hash TEXT",
    }
    for column_name, column_def in notification_columns.items():
        _ensure_column(connection, "notification_deliveries", column_name, column_def)

    _ensure_column(
        connection,
        "prediction_player_impacts",
        "prediction_id",
        "prediction_id INTEGER REFERENCES predictions(id)",
    )
    with connection:
        connection.execute(
            """
            UPDATE predictions
            SET generated_at_utc = COALESCE(
                    generated_at_utc,
                    strftime('%Y-%m-%dT%H:%M:%SZ', generated_at)
                ),
                is_pre_kickoff = COALESCE(
                    is_pre_kickoff,
                    CASE
                        WHEN julianday(generated_at) < julianday(datetime_cdmx) THEN 1
                        ELSE 0
                    END
                )
            WHERE generated_at_utc IS NULL OR is_pre_kickoff IS NULL
            """
        )
        connection.execute(
            """
            UPDATE prediction_player_impacts
            SET prediction_id = (
                SELECT p.id
                FROM predictions p
                WHERE p.match_id = prediction_player_impacts.match_id
                ORDER BY p.id DESC
                LIMIT 1
            )
            WHERE prediction_id IS NULL
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_predictions_match_generated ON predictions(match_id, generated_at_utc)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_impacts_prediction ON prediction_player_impacts(prediction_id)"
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_notification_due
            ON notification_deliveries(status, next_attempt_at, kickoff_at)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_notification_official_open
            ON notification_deliveries(notification_type, match_id, team_norm, status, created_at)
            """
        )


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
            row.get("api_player_id"),
            row.get("api_player_name"),
            row.get("api_position"),
            row.get("height_cm"),
            row.get("nationality"),
            row.get("last_resolved_at"),
            int(row.get("is_active", 1)),
        )
        for row in rosters_df.to_dict(orient="records")
    ]
    _executemany(
        connection,
        """
        INSERT OR REPLACE INTO players (
            team, team_norm, player, player_norm, position_group, club, coach,
            api_player_id, api_player_name, api_position, height_cm, nationality, last_resolved_at, is_active
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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


def upsert_player_api_profile(
    connection: sqlite3.Connection,
    team_norm: str,
    player_name: str,
    player_norm: str,
    position_group: str | None = None,
    api_player_id: int | None = None,
    api_player_name: str | None = None,
    api_position: str | None = None,
    height_cm: float | None = None,
    nationality: str | None = None,
    team_name: str | None = None,
) -> None:
    current = connection.execute(
        """
        SELECT id, team, position_group, club, coach, is_active
        FROM players
        WHERE team_norm = ? AND player_norm = ?
        LIMIT 1
        """,
        (team_norm, player_norm),
    ).fetchone()
    team_value = team_name or (current["team"] if current else team_norm)
    position_value = position_group or (current["position_group"] if current else "unknown")
    club_value = current["club"] if current else None
    coach_value = current["coach"] if current else None
    is_active = int(current["is_active"]) if current else 1
    with connection:
        connection.execute(
            """
            INSERT INTO players (
                team, team_norm, player, player_norm, position_group, club, coach,
                api_player_id, api_player_name, api_position, height_cm, nationality, last_resolved_at, is_active
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?)
            ON CONFLICT(team_norm, player_norm) DO UPDATE SET
                team = excluded.team,
                player = excluded.player,
                position_group = excluded.position_group,
                api_player_id = COALESCE(excluded.api_player_id, players.api_player_id),
                api_player_name = COALESCE(excluded.api_player_name, players.api_player_name),
                api_position = COALESCE(excluded.api_position, players.api_position),
                height_cm = COALESCE(excluded.height_cm, players.height_cm),
                nationality = COALESCE(excluded.nationality, players.nationality),
                last_resolved_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                team_value,
                team_norm,
                player_name,
                player_norm,
                position_value,
                club_value,
                coach_value,
                api_player_id,
                api_player_name,
                api_position,
                height_cm,
                nationality,
                is_active,
            ),
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


def insert_lineup_estimate(
    connection: sqlite3.Connection,
    *,
    fixture_id: str,
    match_id: str,
    team_norm: str,
    window_label: str,
    confidence: float,
    queries: list[str],
    sources: list[dict[str, Any]],
    payload: dict[str, Any],
    generated_at: str,
) -> bool:
    with connection:
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO lineup_estimates (
                fixture_id, match_id, team_norm, window_label, confidence,
                query_json, sources_json, source_json, generated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                fixture_id,
                match_id,
                team_norm,
                window_label,
                confidence,
                json.dumps(queries, ensure_ascii=False),
                json.dumps(sources, ensure_ascii=False),
                json.dumps(payload, ensure_ascii=False),
                generated_at,
            ),
        )
    return cursor.rowcount == 1


def upsert_fixture_player_stats(
    connection: sqlite3.Connection,
    fixture_id: str,
    team_norm: str,
    player_payload: dict[str, Any],
) -> None:
    player_info = player_payload.get("player", {})
    stat_list = player_payload.get("statistics") or []
    stats = stat_list[0] if stat_list else {}
    games = stats.get("games", {})
    shots = stats.get("shots", {})
    goals = stats.get("goals", {})
    passes = stats.get("passes", {})
    tackles = stats.get("tackles", {})
    duels = stats.get("duels", {})
    dribbles = stats.get("dribbles", {})
    fouls = stats.get("fouls", {})
    cards = stats.get("cards", {})
    penalty = stats.get("penalty", {})
    api_player_id = player_info.get("id")
    if api_player_id is None:
        return
    with connection:
        connection.execute(
            """
            INSERT INTO fixture_player_stats (
                fixture_id, api_player_id, team_norm, player_name, player_norm,
                minutes, number, position, rating,
                shots_total, shots_on,
                goals_total, goals_conceded, goals_assists, goals_saves,
                passes_total, passes_key, passes_accuracy,
                tackles_total, tackles_blocks, tackles_interceptions,
                duels_total, duels_won,
                dribbles_attempts, dribbles_success, dribbles_past,
                fouls_drawn, fouls_committed,
                cards_yellow, cards_red,
                penalty_won, penalty_commited, penalty_scored, penalty_missed, penalty_saved,
                source_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(fixture_id, api_player_id, team_norm) DO UPDATE SET
                player_name = excluded.player_name,
                player_norm = excluded.player_norm,
                minutes = excluded.minutes,
                number = excluded.number,
                position = excluded.position,
                rating = excluded.rating,
                shots_total = excluded.shots_total,
                shots_on = excluded.shots_on,
                goals_total = excluded.goals_total,
                goals_conceded = excluded.goals_conceded,
                goals_assists = excluded.goals_assists,
                goals_saves = excluded.goals_saves,
                passes_total = excluded.passes_total,
                passes_key = excluded.passes_key,
                passes_accuracy = excluded.passes_accuracy,
                tackles_total = excluded.tackles_total,
                tackles_blocks = excluded.tackles_blocks,
                tackles_interceptions = excluded.tackles_interceptions,
                duels_total = excluded.duels_total,
                duels_won = excluded.duels_won,
                dribbles_attempts = excluded.dribbles_attempts,
                dribbles_success = excluded.dribbles_success,
                dribbles_past = excluded.dribbles_past,
                fouls_drawn = excluded.fouls_drawn,
                fouls_committed = excluded.fouls_committed,
                cards_yellow = excluded.cards_yellow,
                cards_red = excluded.cards_red,
                penalty_won = excluded.penalty_won,
                penalty_commited = excluded.penalty_commited,
                penalty_scored = excluded.penalty_scored,
                penalty_missed = excluded.penalty_missed,
                penalty_saved = excluded.penalty_saved,
                source_json = excluded.source_json,
                fetched_at = CURRENT_TIMESTAMP
            """,
            (
                str(fixture_id),
                int(api_player_id),
                team_norm,
                str(player_info.get("name") or "Unknown"),
                normalize_text(str(player_info.get("name") or "")),
                games.get("minutes"),
                games.get("number"),
                games.get("position"),
                float(games["rating"]) if games.get("rating") not in (None, "") else None,
                shots.get("total"),
                shots.get("on"),
                goals.get("total"),
                goals.get("conceded"),
                goals.get("assists"),
                goals.get("saves"),
                passes.get("total"),
                passes.get("key"),
                float(passes["accuracy"]) if passes.get("accuracy") not in (None, "") else None,
                tackles.get("total"),
                tackles.get("blocks"),
                tackles.get("interceptions"),
                duels.get("total"),
                duels.get("won"),
                dribbles.get("attempts"),
                dribbles.get("success"),
                dribbles.get("past"),
                fouls.get("drawn"),
                fouls.get("committed"),
                cards.get("yellow"),
                cards.get("red"),
                penalty.get("won"),
                penalty.get("commited"),
                penalty.get("scored"),
                penalty.get("missed"),
                penalty.get("saved"),
                json.dumps(player_payload, ensure_ascii=False),
            ),
        )


def upsert_player_season_stats(
    connection: sqlite3.Connection,
    team_norm: str,
    player_response: dict[str, Any],
) -> None:
    player_info = player_response.get("player", {})
    api_player_id = player_info.get("id")
    if api_player_id is None:
        return
    player_name = str(player_info.get("name") or "Unknown")
    player_norm = normalize_text(player_name)
    try:
        height_cm = float(str(player_info.get("height") or "").replace("cm", "").strip())
    except ValueError:
        height_cm = None
    for stat in player_response.get("statistics") or []:
        league = stat.get("league", {})
        games = stat.get("games", {})
        shots = stat.get("shots", {})
        goals = stat.get("goals", {})
        passes = stat.get("passes", {})
        tackles = stat.get("tackles", {})
        duels = stat.get("duels", {})
        dribbles = stat.get("dribbles", {})
        fouls = stat.get("fouls", {})
        cards = stat.get("cards", {})
        penalty = stat.get("penalty", {})
        with connection:
            connection.execute(
                """
                INSERT INTO player_season_stats (
                    api_player_id, team_norm, player_name, player_norm, season, league_id, league_name,
                    appearences, lineups, minutes, rating,
                    shots_total, shots_on, goals_total, goals_assists,
                    passes_total, passes_key,
                    tackles_total, tackles_interceptions,
                    duels_total, duels_won,
                    dribbles_attempts, dribbles_success,
                    fouls_drawn, fouls_committed,
                    cards_yellow, cards_red,
                    penalty_won, penalty_commited, penalty_scored, penalty_missed, penalty_saved,
                    height_cm, nationality, position, source_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(api_player_id, team_norm, season, league_id) DO UPDATE SET
                    player_name = excluded.player_name,
                    player_norm = excluded.player_norm,
                    appearences = excluded.appearences,
                    lineups = excluded.lineups,
                    minutes = excluded.minutes,
                    rating = excluded.rating,
                    shots_total = excluded.shots_total,
                    shots_on = excluded.shots_on,
                    goals_total = excluded.goals_total,
                    goals_assists = excluded.goals_assists,
                    passes_total = excluded.passes_total,
                    passes_key = excluded.passes_key,
                    tackles_total = excluded.tackles_total,
                    tackles_interceptions = excluded.tackles_interceptions,
                    duels_total = excluded.duels_total,
                    duels_won = excluded.duels_won,
                    dribbles_attempts = excluded.dribbles_attempts,
                    dribbles_success = excluded.dribbles_success,
                    fouls_drawn = excluded.fouls_drawn,
                    fouls_committed = excluded.fouls_committed,
                    cards_yellow = excluded.cards_yellow,
                    cards_red = excluded.cards_red,
                    penalty_won = excluded.penalty_won,
                    penalty_commited = excluded.penalty_commited,
                    penalty_scored = excluded.penalty_scored,
                    penalty_missed = excluded.penalty_missed,
                    penalty_saved = excluded.penalty_saved,
                    height_cm = COALESCE(excluded.height_cm, player_season_stats.height_cm),
                    nationality = COALESCE(excluded.nationality, player_season_stats.nationality),
                    position = COALESCE(excluded.position, player_season_stats.position),
                    source_json = excluded.source_json,
                    fetched_at = CURRENT_TIMESTAMP
                """,
                (
                    int(api_player_id),
                    team_norm,
                    player_name,
                    player_norm,
                    league.get("season"),
                    league.get("id"),
                    league.get("name"),
                    games.get("appearences"),
                    games.get("lineups"),
                    games.get("minutes"),
                    float(games["rating"]) if games.get("rating") not in (None, "") else None,
                    shots.get("total"),
                    shots.get("on"),
                    goals.get("total"),
                    goals.get("assists"),
                    passes.get("total"),
                    passes.get("key"),
                    tackles.get("total"),
                    tackles.get("interceptions"),
                    duels.get("total"),
                    duels.get("won"),
                    dribbles.get("attempts"),
                    dribbles.get("success"),
                    fouls.get("drawn"),
                    fouls.get("committed"),
                    cards.get("yellow"),
                    cards.get("red"),
                    penalty.get("won"),
                    penalty.get("commited"),
                    penalty.get("scored"),
                    penalty.get("missed"),
                    penalty.get("saved"),
                    height_cm,
                    player_info.get("nationality"),
                    games.get("position"),
                    json.dumps(player_response, ensure_ascii=False),
                ),
            )


def insert_prediction_player_impacts(
    connection: sqlite3.Connection,
    prediction_id: int,
    match_id: str,
    impact_rows: list[dict[str, Any]],
) -> None:
    with connection:
        for row in impact_rows:
            connection.execute(
                """
                INSERT INTO prediction_player_impacts (
                    prediction_id, match_id, team_norm, api_player_id, player_name, role_bucket,
                    attack_impact, defense_impact, discipline_impact, availability_impact,
                    net_impact, source_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    prediction_id,
                    match_id,
                    row.get("team_norm"),
                    row.get("api_player_id"),
                    row.get("player_name"),
                    row.get("role_bucket", "unknown"),
                    float(row.get("attack_impact", 0.0)),
                    float(row.get("defense_impact", 0.0)),
                    float(row.get("discipline_impact", 0.0)),
                    float(row.get("availability_impact", 0.0)),
                    float(row.get("net_impact", 0.0)),
                    json.dumps(row, ensure_ascii=False),
                ),
            )


def insert_prediction_rows(
    connection: sqlite3.Connection,
    predictions_df: pd.DataFrame,
) -> list[int]:
    inserted_ids: list[int] = []
    with connection:
        for row in predictions_df.to_dict(orient="records"):
            cursor = connection.execute(
                """
                INSERT INTO predictions (
                    match_id, datetime_cdmx, group_name, home_team, away_team,
                    predicted_score, probability, model_version,
                    hybrid_predicted_score, hybrid_probability,
                    home_win_probability, draw_probability, away_win_probability,
                    outcome_model_version, data_freshness_at, source_json,
                    prediction_context, window_label, generated_at_utc,
                    is_pre_kickoff
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row.get("match_id"),
                    row.get("datetime_cdmx"),
                    row.get("group"),
                    row.get("home_team"),
                    row.get("away_team"),
                    row.get("predicted_score"),
                    float(row.get("probability", 0.0)),
                    row.get("model_version"),
                    row.get("hybrid_predicted_score"),
                    float(row.get("hybrid_probability", 0.0))
                    if row.get("hybrid_probability") is not None
                    else None,
                    float(row.get("home_win_probability", 0.0))
                    if row.get("home_win_probability") is not None
                    else None,
                    float(row.get("draw_probability", 0.0))
                    if row.get("draw_probability") is not None
                    else None,
                    float(row.get("away_win_probability", 0.0))
                    if row.get("away_win_probability") is not None
                    else None,
                    row.get("outcome_model_version"),
                    row.get("data_freshness_at"),
                    json.dumps(row, ensure_ascii=False),
                    row.get("prediction_context", "manual"),
                    row.get("window_label"),
                    row.get("generated_at_utc") or row.get("generated_at"),
                    int(row.get("is_pre_kickoff", 0)),
                ),
            )
            inserted_ids.append(int(cursor.lastrowid))
    return inserted_ids


def claim_automation_run(
    connection: sqlite3.Connection,
    run_key: str,
    action: str,
    scheduled_for: str,
    match_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> bool:
    try:
        with connection:
            connection.execute(
                """
                INSERT INTO automation_runs (
                    run_key, action, match_id, scheduled_for, status, details_json
                ) VALUES (?, ?, ?, ?, 'running', ?)
                """,
                (
                    run_key,
                    action,
                    match_id,
                    scheduled_for,
                    json.dumps(details or {}, ensure_ascii=False),
                ),
            )
    except sqlite3.IntegrityError:
        return False
    return True


def finish_automation_run(
    connection: sqlite3.Connection,
    run_key: str,
    status: str,
    details: dict[str, Any] | None = None,
) -> None:
    with connection:
        connection.execute(
            """
            UPDATE automation_runs
            SET status = ?,
                details_json = ?,
                finished_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE run_key = ?
            """,
            (
                status,
                json.dumps(details or {}, ensure_ascii=False),
                run_key,
            ),
        )


def insert_pre_match_snapshot(
    connection: sqlite3.Connection,
    snapshot: dict[str, Any],
    player_rows: list[dict[str, Any]],
) -> tuple[int, bool]:
    with connection:
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO pre_match_snapshots (
                match_id, fixture_id, window_label, source_kind,
                kickoff_at, captured_at, features_json, prediction_json,
                home_lineup_source, away_lineup_source,
                model_version, outcome_model_version, data_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot["match_id"],
                snapshot.get("fixture_id"),
                snapshot["window_label"],
                snapshot["source_kind"],
                snapshot["kickoff_at"],
                snapshot["captured_at"],
                snapshot["features_json"],
                snapshot["prediction_json"],
                snapshot.get("home_lineup_source"),
                snapshot.get("away_lineup_source"),
                snapshot.get("model_version"),
                snapshot.get("outcome_model_version"),
                snapshot["data_hash"],
            ),
        )
        created = cursor.rowcount == 1
        row = connection.execute(
            """
            SELECT id FROM pre_match_snapshots
            WHERE match_id = ? AND window_label = ? AND source_kind = ?
            """,
            (
                snapshot["match_id"],
                snapshot["window_label"],
                snapshot["source_kind"],
            ),
        ).fetchone()
        snapshot_id = int(row["id"])
        if created:
            connection.executemany(
                """
                INSERT OR IGNORE INTO pre_match_player_snapshots (
                    snapshot_id, team_norm, api_player_id, player_name, player_norm,
                    lineup_role, role_bucket, attack_impact, defense_impact,
                    discipline_impact, availability_impact, net_impact, features_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        snapshot_id,
                        player["team_norm"],
                        player.get("api_player_id"),
                        player["player_name"],
                        player.get("player_norm"),
                        player.get("lineup_role", "unknown"),
                        player.get("role_bucket", "unknown"),
                        float(player.get("attack_impact", 0.0)),
                        float(player.get("defense_impact", 0.0)),
                        float(player.get("discipline_impact", 0.0)),
                        float(player.get("availability_impact", 0.0)),
                        float(player.get("net_impact", 0.0)),
                        json.dumps(player, ensure_ascii=False, default=str),
                    )
                    for player in player_rows
                ],
            )
    return snapshot_id, created


def get_latest_pre_match_snapshot(
    connection: sqlite3.Connection,
    match_id: str,
    before_kickoff: str | None = None,
) -> sqlite3.Row | None:
    query = "SELECT * FROM pre_match_snapshots WHERE match_id = ?"
    params: list[Any] = [match_id]
    if before_kickoff:
        query += " AND julianday(captured_at) < julianday(?)"
        params.append(before_kickoff)
    query += " ORDER BY julianday(captured_at) DESC, id DESC LIMIT 1"
    return connection.execute(query, params).fetchone()


def insert_player_targets(
    connection: sqlite3.Connection,
    rows: list[dict[str, Any]],
) -> int:
    if not rows:
        return 0
    columns = [
        "match_id", "fixture_id", "snapshot_id", "team_norm", "api_player_id",
        "player_name", "player_norm", "participated", "minutes", "rating",
        "shots_total", "shots_on", "goals_total", "goals_assists",
        "dribbles_attempts", "dribbles_success", "passes_total", "passes_key",
        "passes_accuracy", "tackles_total", "tackles_blocks",
        "tackles_interceptions", "duels_total", "duels_won", "goals_saves",
        "goals_conceded", "fouls_committed", "cards_yellow", "cards_red",
        "penalty_won", "penalty_commited", "penalty_scored", "penalty_missed",
        "penalty_saved", "target_json",
    ]
    placeholders = ",".join("?" for _ in columns)
    before = connection.total_changes
    with connection:
        connection.executemany(
            f"""
            INSERT OR IGNORE INTO player_match_targets ({",".join(columns)})
            VALUES ({placeholders})
            """,
            [
                tuple(
                    json.dumps(
                        {
                            key: value
                            for key, value in row.items()
                            if key != "target_json"
                        },
                        ensure_ascii=False,
                        default=str,
                    )
                    if column == "target_json"
                    else row.get(column)
                    for column in columns
                )
                for row in rows
            ],
        )
    return connection.total_changes - before


def register_model_release(
    connection: sqlite3.Connection,
    release: dict[str, Any],
) -> None:
    with connection:
        connection.execute(
            """
            INSERT INTO model_releases (
                release_id, release_path, status, model_version, dataset_hash,
                cutoff_at, training_matches, live_matches, metrics_json, preliminary
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(release_id) DO UPDATE SET
                release_path = excluded.release_path,
                status = excluded.status,
                metrics_json = excluded.metrics_json,
                preliminary = excluded.preliminary
            """,
            (
                release["release_id"],
                release["release_path"],
                release.get("status", "candidate"),
                release["model_version"],
                release["dataset_hash"],
                release["cutoff_at"],
                int(release["training_matches"]),
                int(release.get("live_matches", 0)),
                json.dumps(release.get("metrics", {}), ensure_ascii=False, default=str),
                int(bool(release.get("preliminary", True))),
            ),
        )


def activate_model_release(connection: sqlite3.Connection, release_id: str) -> bool:
    candidate = connection.execute(
        "SELECT release_id FROM model_releases WHERE release_id = ?",
        (release_id,),
    ).fetchone()
    if candidate is None:
        return False
    with connection:
        active = connection.execute(
            "SELECT release_id FROM model_releases WHERE status = 'active' LIMIT 1"
        ).fetchone()
        previous_id = str(active["release_id"]) if active else None
        connection.execute(
            """
            UPDATE model_releases
            SET status = 'archived'
            WHERE status = 'active' AND release_id <> ?
            """,
            (release_id,),
        )
        connection.execute(
            """
            UPDATE model_releases
            SET status = 'active',
                previous_release_id = ?,
                activated_at = CURRENT_TIMESTAMP
            WHERE release_id = ?
            """,
            (previous_id, release_id),
        )
    return True


def get_active_model_release(connection: sqlite3.Connection) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT * FROM model_releases
        WHERE status = 'active'
        ORDER BY activated_at DESC, id DESC
        LIMIT 1
        """
    ).fetchone()


def fetch_dataframe(connection: sqlite3.Connection, query: str, params: Iterable[Any] | None = None) -> pd.DataFrame:
    return pd.read_sql_query(query, connection, params=params or ())
