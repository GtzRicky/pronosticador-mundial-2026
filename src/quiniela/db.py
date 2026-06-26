from __future__ import annotations

from pathlib import Path
import json
import sqlite3
from typing import Any, Iterable

import pandas as pd

from quiniela.adapters.outbound.sqlite.connection import connect_sqlite
from quiniela.adapters.outbound.sqlite.migrations import apply_schema
from quiniela.adapters.outbound.sqlite.prediction_repository import SQLitePredictionRepository
from quiniela.adapters.outbound.sqlite.snapshot_repository import SQLiteSnapshotRepository
from quiniela.cache import canonical_json, hash_params
from quiniela.config import get_settings
from quiniela.infrastructure.competition_config import CompetitionContext
from quiniela.name_maps import normalize_team_name, normalize_text


DEFAULT_COMPETITION_ID = "fifa_world_cup"
DEFAULT_SEASON_ID = "world_cup_2026"


SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS competitions (
        competition_id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        sport TEXT NOT NULL,
        organizer TEXT NOT NULL,
        competition_type TEXT NOT NULL,
        default_timezone TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS seasons (
        season_id TEXT PRIMARY KEY,
        competition_id TEXT NOT NULL,
        name TEXT NOT NULL,
        start_date TEXT NOT NULL,
        end_date TEXT NOT NULL,
        timezone TEXT NOT NULL,
        status TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(competition_id) REFERENCES competitions(competition_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS competition_participants (
        participant_id TEXT PRIMARY KEY,
        competition_id TEXT NOT NULL,
        season_id TEXT NOT NULL,
        team_norm TEXT NOT NULL,
        display_name TEXT NOT NULL,
        short_name TEXT,
        group_key TEXT,
        seed INTEGER,
        active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(season_id, team_norm),
        FOREIGN KEY(competition_id) REFERENCES competitions(competition_id),
        FOREIGN KEY(season_id) REFERENCES seasons(season_id)
    )
    """,
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
    CREATE TABLE IF NOT EXISTS odds_market_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fixture_id TEXT NOT NULL,
        match_id TEXT,
        bookmaker_id INTEGER,
        bookmaker TEXT,
        bet_id INTEGER,
        market_key TEXT NOT NULL,
        market_name TEXT NOT NULL,
        selection_key TEXT NOT NULL,
        selection_name TEXT NOT NULL,
        selection_team_norm TEXT,
        line_key TEXT NOT NULL DEFAULT '',
        handicap TEXT,
        decimal_odd REAL NOT NULL,
        implied_probability REAL NOT NULL,
        suspended INTEGER NOT NULL DEFAULT 0,
        source_update TEXT NOT NULL DEFAULT '',
        source_json TEXT NOT NULL,
        fetched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(fixture_id, bookmaker_id, market_key, selection_key, line_key, source_update)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS odds_market_consensus (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fixture_id TEXT NOT NULL,
        match_id TEXT,
        market_key TEXT NOT NULL,
        market_name TEXT NOT NULL,
        selection_key TEXT NOT NULL,
        selection_name TEXT NOT NULL,
        line_key TEXT NOT NULL DEFAULT '',
        consensus_probability REAL NOT NULL,
        median_decimal_odd REAL,
        bookmaker_count INTEGER NOT NULL,
        overround_method TEXT NOT NULL,
        source_json TEXT NOT NULL,
        calculated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(fixture_id, market_key, selection_key, line_key)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS odds_model_predictions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fixture_id TEXT NOT NULL,
        match_id TEXT,
        model_version TEXT NOT NULL,
        market_key TEXT NOT NULL,
        prediction_key TEXT NOT NULL,
        probability REAL NOT NULL,
        source_json TEXT NOT NULL,
        calculated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(fixture_id, model_version, market_key, prediction_key)
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
        audit_snapshot_id INTEGER,
        audit_lineup_sources_json TEXT,
        audit_odds_source_json TEXT,
        audit_degradation_reasons_json TEXT NOT NULL DEFAULT '[]',
        not_evaluable_reason TEXT,
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
        dedupe_key TEXT,
        max_attempts INTEGER NOT NULL DEFAULT 5,
        template_version TEXT NOT NULL DEFAULT 'notification_v1',
        payload_hash TEXT,
        expires_at TEXT,
        last_error_message TEXT,
        channel_priority TEXT,
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
    return connect_sqlite(path, schema_initializer=create_schema)


def create_schema(connection: sqlite3.Connection) -> None:
    apply_schema(connection, SCHEMA_STATEMENTS, migrate=_migrate_schema)


def _table_columns(connection: sqlite3.Connection, table_name: str) -> set[str]:
    rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {str(row["name"]) for row in rows}


def _ensure_column(connection: sqlite3.Connection, table_name: str, column_name: str, column_def: str) -> None:
    if column_name in _table_columns(connection, table_name):
        return
    with connection:
        connection.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_def}")


def _create_index_if_columns(
    connection: sqlite3.Connection,
    *,
    table_name: str,
    required_columns: set[str],
    statement: str,
) -> None:
    if not required_columns.issubset(_table_columns(connection, table_name)):
        return
    connection.execute(statement)


def _ensure_multi_tournament_columns(connection: sqlite3.Connection) -> None:
    scoped_tables = {
        "players",
        "matches",
        "lineup_estimates",
        "odds_snapshots",
        "odds_market_snapshots",
        "odds_market_consensus",
        "odds_model_predictions",
        "predictions",
        "actual_results",
        "prediction_player_impacts",
        "prediction_evaluations",
        "notification_deliveries",
        "pre_match_snapshots",
        "pre_match_player_snapshots",
        "player_match_targets",
        "player_evidence_evaluations",
        "player_prediction_evaluations",
        "model_training_runs",
        "model_releases",
    }
    for table_name in scoped_tables:
        _ensure_column(
            connection,
            table_name,
            "competition_id",
            f"competition_id TEXT NOT NULL DEFAULT '{DEFAULT_COMPETITION_ID}'",
        )
        _ensure_column(
            connection,
            table_name,
            "season_id",
            f"season_id TEXT NOT NULL DEFAULT '{DEFAULT_SEASON_ID}'",
        )


def _seed_default_competition(connection: sqlite3.Connection) -> None:
    with connection:
        connection.execute(
            """
            INSERT INTO competitions (
                competition_id, name, sport, organizer, competition_type, default_timezone
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(competition_id) DO UPDATE SET
                name = excluded.name,
                sport = excluded.sport,
                organizer = excluded.organizer,
                competition_type = excluded.competition_type,
                default_timezone = excluded.default_timezone,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                DEFAULT_COMPETITION_ID,
                "FIFA World Cup",
                "football",
                "FIFA",
                "national_teams",
                "America/Mexico_City",
            ),
        )
        connection.execute(
            """
            INSERT INTO seasons (
                season_id, competition_id, name, start_date, end_date, timezone, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(season_id) DO UPDATE SET
                competition_id = excluded.competition_id,
                name = excluded.name,
                start_date = excluded.start_date,
                end_date = excluded.end_date,
                timezone = excluded.timezone,
                status = excluded.status,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                DEFAULT_SEASON_ID,
                DEFAULT_COMPETITION_ID,
                "FIFA World Cup 2026",
                "2026-06-11",
                "2026-07-19",
                "America/Mexico_City",
                "planned",
            ),
        )


def _backfill_default_scope(connection: sqlite3.Connection) -> None:
    with connection:
        for table_name in {
            "players",
            "matches",
            "lineup_estimates",
            "odds_snapshots",
            "odds_market_snapshots",
            "odds_market_consensus",
            "odds_model_predictions",
            "predictions",
            "actual_results",
            "prediction_player_impacts",
            "prediction_evaluations",
            "notification_deliveries",
            "pre_match_snapshots",
            "pre_match_player_snapshots",
            "player_match_targets",
            "player_evidence_evaluations",
            "player_prediction_evaluations",
            "model_training_runs",
            "model_releases",
        }:
            connection.execute(
                f"""
                UPDATE {table_name}
                SET competition_id = COALESCE(NULLIF(competition_id, ''), ?),
                    season_id = COALESCE(NULLIF(season_id, ''), ?)
                WHERE competition_id IS NULL
                   OR competition_id = ''
                   OR season_id IS NULL
                   OR season_id = ''
                """,
                (DEFAULT_COMPETITION_ID, DEFAULT_SEASON_ID),
            )


def _sync_default_competition_participants(connection: sqlite3.Connection) -> None:
    with connection:
        connection.execute(
            """
            INSERT INTO competition_participants (
                participant_id, competition_id, season_id, team_norm, display_name, short_name, group_key, active
            )
            SELECT
                ? || ':' || team_norm,
                ?,
                ?,
                team_norm,
                team_name,
                team_name,
                NULL,
                1
            FROM teams
            WHERE team_norm IS NOT NULL AND team_norm != ''
            ON CONFLICT(season_id, team_norm) DO UPDATE SET
                display_name = excluded.display_name,
                short_name = excluded.short_name,
                active = excluded.active,
                updated_at = CURRENT_TIMESTAMP
            """,
            (DEFAULT_SEASON_ID, DEFAULT_COMPETITION_ID, DEFAULT_SEASON_ID),
        )


def _migrate_schema(connection: sqlite3.Connection) -> None:
    _seed_default_competition(connection)
    _ensure_multi_tournament_columns(connection)
    _backfill_default_scope(connection)
    _sync_default_competition_participants(connection)

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
        "audit_snapshot_id": "audit_snapshot_id INTEGER",
        "audit_lineup_sources_json": "audit_lineup_sources_json TEXT",
        "audit_odds_source_json": "audit_odds_source_json TEXT",
        "audit_degradation_reasons_json": "audit_degradation_reasons_json TEXT NOT NULL DEFAULT '[]'",
        "not_evaluable_reason": "not_evaluable_reason TEXT",
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
        "dedupe_key": "dedupe_key TEXT",
        "max_attempts": "max_attempts INTEGER NOT NULL DEFAULT 5",
        "template_version": "template_version TEXT NOT NULL DEFAULT 'notification_v1'",
        "payload_hash": "payload_hash TEXT",
        "expires_at": "expires_at TEXT",
        "last_error_message": "last_error_message TEXT",
        "channel_priority": "channel_priority TEXT",
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
            UPDATE notification_deliveries
            SET dedupe_key = COALESCE(
                    dedupe_key,
                    COALESCE(notification_type, 'prediction_window') || ':' ||
                    match_id || ':' || kickoff_at || ':' || window_label || ':' || channel
                ),
                expires_at = COALESCE(expires_at, kickoff_at),
                max_attempts = COALESCE(max_attempts, 5),
                template_version = COALESCE(template_version, 'notification_v1')
            WHERE dedupe_key IS NULL
               OR expires_at IS NULL
               OR max_attempts IS NULL
               OR template_version IS NULL
            """
        )
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_notification_dedupe_key
            ON notification_deliveries(dedupe_key)
            WHERE dedupe_key IS NOT NULL
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_notification_official_open
            ON notification_deliveries(notification_type, match_id, team_norm, status, created_at)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_odds_market_snapshots_fixture
            ON odds_market_snapshots(fixture_id, market_key, line_key)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_odds_consensus_fixture
            ON odds_market_consensus(fixture_id, market_key, line_key)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_competition_participants_season_team
            ON competition_participants(season_id, team_norm)
            """
        )
        _create_index_if_columns(
            connection,
            table_name="matches",
            required_columns={"season_id", "date_cdmx"},
            statement="""
                CREATE INDEX IF NOT EXISTS idx_matches_season_date
                ON matches(season_id, date_cdmx)
            """,
        )
        _create_index_if_columns(
            connection,
            table_name="predictions",
            required_columns={"season_id", "match_id", "generated_at_utc"},
            statement="""
                CREATE INDEX IF NOT EXISTS idx_predictions_season_match
                ON predictions(season_id, match_id, generated_at_utc)
            """,
        )
        _create_index_if_columns(
            connection,
            table_name="odds_market_consensus",
            required_columns={"season_id", "fixture_id", "market_key", "line_key"},
            statement="""
                CREATE INDEX IF NOT EXISTS idx_odds_consensus_season_fixture
                ON odds_market_consensus(season_id, fixture_id, market_key, line_key)
            """,
        )
        _create_index_if_columns(
            connection,
            table_name="notification_deliveries",
            required_columns={"season_id", "status", "next_attempt_at", "kickoff_at"},
            statement="""
                CREATE INDEX IF NOT EXISTS idx_notification_season_due
                ON notification_deliveries(season_id, status, next_attempt_at, kickoff_at)
            """,
        )


def _executemany(connection: sqlite3.Connection, query: str, rows: Iterable[tuple[Any, ...]]) -> None:
    rows = list(rows)
    if not rows:
        return
    with connection:
        connection.executemany(query, rows)


def ensure_competition_scope(
    connection: sqlite3.Connection,
    context: CompetitionContext,
) -> None:
    config = context.config
    with connection:
        connection.execute(
            """
            INSERT INTO competitions (
                competition_id, name, sport, organizer, competition_type, default_timezone
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(competition_id) DO UPDATE SET
                name = excluded.name,
                sport = excluded.sport,
                organizer = excluded.organizer,
                competition_type = excluded.competition_type,
                default_timezone = excluded.default_timezone,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                context.competition_id,
                config.competition.name,
                config.competition.sport,
                config.competition.organizer,
                config.competition.competition_type,
                config.competition.default_timezone,
            ),
        )
        connection.execute(
            """
            INSERT INTO seasons (
                season_id, competition_id, name, start_date, end_date, timezone, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(season_id) DO UPDATE SET
                competition_id = excluded.competition_id,
                name = excluded.name,
                start_date = excluded.start_date,
                end_date = excluded.end_date,
                timezone = excluded.timezone,
                status = excluded.status,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                context.season_id,
                context.competition_id,
                config.season.name,
                config.season.start_date.isoformat(),
                config.season.end_date.isoformat(),
                config.season.timezone,
                config.season.status,
            ),
        )


def load_matches(
    connection: sqlite3.Connection,
    matches_df: pd.DataFrame,
    context: CompetitionContext | None = None,
) -> int:
    competition_id = context.competition_id if context else DEFAULT_COMPETITION_ID
    season_id = context.season_id if context else DEFAULT_SEASON_ID
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
            normalize_team_name(str(row["home_team"]), strict=True),
            normalize_team_name(str(row["away_team"]), strict=True),
            row["group"],
            row["stadium"],
            row["stage"],
            row["status"],
            competition_id,
            season_id,
        )
        for row in matches_df.to_dict(orient="records")
    ]
    _executemany(
        connection,
        """
        INSERT OR REPLACE INTO matches (
            match_id, date_et, time_et, datetime_et, date_cdmx, time_cdmx, datetime_cdmx,
            home_team, away_team, home_team_norm, away_team_norm, group_name, stadium, stage, status,
            competition_id, season_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    return len(rows)


def load_players(
    connection: sqlite3.Connection,
    rosters_df: pd.DataFrame,
    context: CompetitionContext | None = None,
) -> int:
    competition_id = context.competition_id if context else DEFAULT_COMPETITION_ID
    season_id = context.season_id if context else DEFAULT_SEASON_ID
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
            competition_id,
            season_id,
        )
        for row in rosters_df.to_dict(orient="records")
    ]
    _executemany(
        connection,
        """
        INSERT OR REPLACE INTO players (
            team, team_norm, player, player_norm, position_group, club, coach,
            api_player_id, api_player_name, api_position, height_cm, nationality, last_resolved_at, is_active,
            competition_id, season_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    return len(rows)


def load_teams(
    connection: sqlite3.Connection,
    matches_df: pd.DataFrame,
    rosters_df: pd.DataFrame,
    context: CompetitionContext | None = None,
) -> int:
    teams: dict[str, str] = {}

    for row in matches_df.to_dict(orient="records"):
        teams.setdefault(
            normalize_team_name(str(row["home_team"]), strict=True),
            row["home_team"],
        )
        teams.setdefault(
            normalize_team_name(str(row["away_team"]), strict=True),
            row["away_team"],
        )

    for row in rosters_df.to_dict(orient="records"):
        teams.setdefault(normalize_team_name(str(row["team"]), strict=True), row["team"])

    rows = [(name, norm) for norm, name in sorted(teams.items())]
    _executemany(
        connection,
        "INSERT OR IGNORE INTO teams (team_name, team_norm) VALUES (?, ?)",
        rows,
    )
    if context is None:
        _sync_default_competition_participants(connection)
    else:
        ensure_competition_scope(connection, context)
        with connection:
            connection.executemany(
                """
                INSERT INTO competition_participants (
                    participant_id, competition_id, season_id, team_norm, display_name, active
                ) VALUES (?, ?, ?, ?, ?, 1)
                ON CONFLICT(season_id, team_norm) DO UPDATE SET
                    display_name = excluded.display_name,
                    active = 1,
                    updated_at = CURRENT_TIMESTAMP
                """,
                [
                    (
                        f"{context.season_id}:{norm}",
                        context.competition_id,
                        context.season_id,
                        norm,
                        name,
                    )
                    for norm, name in sorted(teams.items())
                ],
            )
    return len(rows)


def seed_from_processed(
    connection: sqlite3.Connection,
    calendar_path: Path | None = None,
    rosters_path: Path | None = None,
    context: CompetitionContext | None = None,
) -> dict[str, int]:
    settings = get_settings()
    calendar_path = calendar_path or settings.processed_dir / "calendar.csv"
    rosters_path = rosters_path or settings.processed_dir / "rosters.csv"
    matches_df = pd.read_csv(calendar_path)
    rosters_df = pd.read_csv(rosters_path)
    competition_id = context.competition_id if context else DEFAULT_COMPETITION_ID
    season_id = context.season_id if context else DEFAULT_SEASON_ID
    if context is not None:
        ensure_competition_scope(connection, context)
    with connection:
        connection.execute(
            "DELETE FROM players WHERE competition_id = ? AND season_id = ?",
            (competition_id, season_id),
        )
        connection.execute(
            "DELETE FROM matches WHERE competition_id = ? AND season_id = ?",
            (competition_id, season_id),
        )
        connection.execute(
            "DELETE FROM competition_participants WHERE season_id = ?",
            (season_id,),
        )
    return {
        "teams": load_teams(connection, matches_df, rosters_df, context=context),
        "players": load_players(connection, rosters_df, context=context),
        "matches": load_matches(connection, matches_df, context=context),
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
    return SQLitePredictionRepository(connection).insert_prediction_rows(predictions_df)


def claim_automation_run(
    connection: sqlite3.Connection,
    run_key: str,
    action: str,
    scheduled_for: str,
    match_id: str | None = None,
    details: dict[str, Any] | None = None,
    stale_after_minutes: int = 25,
) -> bool:
    details_payload = json.dumps(details or {}, ensure_ascii=False)
    stale_modifier = f"-{int(stale_after_minutes)} minutes"
    with connection:
        existing = connection.execute(
            """
            SELECT status, details_json
            FROM automation_runs
            WHERE run_key = ?
            """,
            (run_key,),
        ).fetchone()
        if existing is not None:
            if str(existing["status"]) != "running":
                try:
                    existing_details = json.loads(existing["details_json"] or "{}")
                except json.JSONDecodeError:
                    existing_details = {}
                if (
                    str(existing["status"]) == "failed"
                    and existing_details.get("error_code")
                    == "stale_automation_run_recovered"
                ):
                    cursor = connection.execute(
                        """
                        UPDATE automation_runs
                        SET action = ?,
                            match_id = ?,
                            scheduled_for = ?,
                            status = 'running',
                            details_json = ?,
                            started_at = CURRENT_TIMESTAMP,
                            finished_at = NULL,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE run_key = ?
                          AND status = 'failed'
                        """,
                        (
                            action,
                            match_id,
                            scheduled_for,
                            details_payload,
                            run_key,
                        ),
                    )
                    return cursor.rowcount == 1
                return False
            cursor = connection.execute(
                """
                UPDATE automation_runs
                SET action = ?,
                    match_id = ?,
                    scheduled_for = ?,
                    details_json = ?,
                    started_at = CURRENT_TIMESTAMP,
                    finished_at = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE run_key = ?
                  AND status = 'running'
                  AND julianday(started_at) <= julianday('now', ?)
                """,
                (
                    action,
                    match_id,
                    scheduled_for,
                    details_payload,
                    run_key,
                    stale_modifier,
                ),
            )
            return cursor.rowcount == 1

        try:
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
                    details_payload,
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
    def json_default(value: Any) -> Any:
        if isinstance(value, pd.DataFrame):
            return value.to_dict(orient="records")
        if isinstance(value, pd.Series):
            return value.to_dict()
        if isinstance(value, pd.Timestamp):
            return value.isoformat()
        if isinstance(value, Path):
            return str(value)
        item = getattr(value, "item", None)
        if callable(item):
            return item()
        return str(value)

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
                json.dumps(details or {}, ensure_ascii=False, default=json_default),
                run_key,
            ),
        )


def recover_stale_automation_runs(
    connection: sqlite3.Connection,
    *,
    stale_after_minutes: int = 25,
    apply: bool = False,
) -> dict[str, Any]:
    stale_modifier = f"-{int(stale_after_minutes)} minutes"
    rows = connection.execute(
        """
        SELECT run_key, action, match_id, scheduled_for, details_json, started_at
        FROM automation_runs
        WHERE status = 'running'
          AND finished_at IS NULL
          AND julianday(started_at) <= julianday('now', ?)
        ORDER BY started_at
        """,
        (stale_modifier,),
    ).fetchall()
    recovered: list[dict[str, Any]] = []
    for row in rows:
        try:
            original_details = json.loads(row["details_json"] or "{}")
        except json.JSONDecodeError:
            original_details = {"raw_details_json": row["details_json"]}
        details = {
            **original_details,
            "error": "stale_automation_run_recovered",
            "error_code": "stale_automation_run_recovered",
            "reason": "stale_automation_run_recovered",
            "stale_after_minutes": int(stale_after_minutes),
            "original_started_at": row["started_at"],
        }
        recovered.append(
            {
                "run_key": row["run_key"],
                "action": row["action"],
                "match_id": row["match_id"],
                "started_at": row["started_at"],
            }
        )
        if apply:
            with connection:
                connection.execute(
                    """
                    UPDATE automation_runs
                    SET status = 'failed',
                        details_json = ?,
                        finished_at = CURRENT_TIMESTAMP,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE run_key = ?
                      AND status = 'running'
                    """,
                    (
                        json.dumps(details, ensure_ascii=False),
                        row["run_key"],
                    ),
                )
    return {
        "apply": bool(apply),
        "stale_after_minutes": int(stale_after_minutes),
        "recovered": len(recovered),
        "runs": recovered,
    }


def insert_pre_match_snapshot(
    connection: sqlite3.Connection,
    snapshot: dict[str, Any],
    player_rows: list[dict[str, Any]],
) -> tuple[int, bool]:
    return SQLiteSnapshotRepository(connection).insert_pre_match_snapshot(snapshot, player_rows)


def get_latest_pre_match_snapshot(
    connection: sqlite3.Connection,
    match_id: str,
    before_kickoff: str | None = None,
) -> sqlite3.Row | None:
    return SQLiteSnapshotRepository(connection).get_latest_pre_match_snapshot(match_id, before_kickoff)


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
