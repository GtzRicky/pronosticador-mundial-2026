from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import json
import shutil
import sqlite3
import tempfile
import zipfile

from quiniela.config import get_settings
from quiniela.db import fetch_dataframe, get_connection
from quiniela.infrastructure.competition_config import (
    CompetitionContext,
    resolve_competition_context,
)


PUBLIC_DB_NAME = "quiniela_public.sqlite"
MANIFEST_NAME = "manifest.json"


@dataclass(frozen=True)
class BundleSummary:
    bundle_dir: Path
    archive_path: Path | None
    manifest_path: Path
    db_path: Path
    copied_processed_files: list[Path]
    table_counts: dict[str, int]


def _table_counts(connection: sqlite3.Connection) -> dict[str, int]:
    tables = [
        "competitions",
        "seasons",
        "competition_participants",
        "teams",
        "players",
        "matches",
        "historical_matches",
        "historical_lineups",
        "lineup_estimates",
        "historical_team_stats",
        "historical_player_stats",
        "odds_snapshots",
        "predictions",
        "prediction_player_impacts",
        "prediction_evaluations",
        "actual_results",
        "automation_runs",
        "notification_deliveries",
        "pre_match_snapshots",
        "pre_match_player_snapshots",
        "player_match_targets",
        "player_evidence_evaluations",
        "player_prediction_evaluations",
        "model_training_runs",
        "model_releases",
        "api_cache",
        "api_usage",
    ]
    counts: dict[str, int] = {}
    for table in tables:
        try:
            row = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
            counts[table] = int(row[0]) if row is not None else 0
        except sqlite3.Error:
            counts[table] = 0
    return counts


def _public_manifest(
    connection: sqlite3.Connection,
    competition_context: CompetitionContext,
) -> dict[str, object]:
    settings = get_settings()
    counts = _table_counts(connection)
    return {
        "bundle_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "db_file": PUBLIC_DB_NAME,
        "processed_files": ["processed/calendar.csv", "processed/rosters.csv"],
        "table_counts": counts,
        "excluded_tables": ["api_cache", "api_usage", "notification_deliveries"],
        "notes": [
            "This bundle is designed for offline prediction bootstrap.",
            "No API key is included in this bundle.",
            "Consumers may still use API-Football for day-of-match lineups, statistics, and odds.",
            "Review third-party data licensing before redistributing derived or raw sports data.",
        ],
        "local_timezone": settings.local_timezone,
        "competition_id": competition_context.competition_id,
        "season_id": competition_context.season_id,
        "namespace": competition_context.namespace,
    }


def _scope_public_database(
    connection: sqlite3.Connection,
    competition_context: CompetitionContext,
) -> None:
    existing_tables = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    if "matches" not in existing_tables:
        return
    competition_id = competition_context.competition_id
    season_id = competition_context.season_id
    with connection:
        if {"prediction_player_impacts", "predictions"} <= existing_tables:
            connection.execute(
            """
            DELETE FROM prediction_player_impacts
            WHERE prediction_id NOT IN (
                SELECT id FROM predictions
                WHERE competition_id = ? AND season_id = ?
            )
            """,
            (competition_id, season_id),
        )
        if {"prediction_evaluations", "predictions"} <= existing_tables:
            connection.execute(
            """
            DELETE FROM prediction_evaluations
            WHERE prediction_id NOT IN (
                SELECT id FROM predictions
                WHERE competition_id = ? AND season_id = ?
            )
            """,
            (competition_id, season_id),
        )
        if "actual_results" in existing_tables:
            connection.execute(
            """
            DELETE FROM actual_results
            WHERE match_id NOT IN (
                SELECT match_id FROM matches
                WHERE competition_id = ? AND season_id = ?
            )
            """,
            (competition_id, season_id),
        )
        if {"pre_match_player_snapshots", "pre_match_snapshots"} <= existing_tables:
            connection.execute(
            """
            DELETE FROM pre_match_player_snapshots
            WHERE snapshot_id NOT IN (
                SELECT id FROM pre_match_snapshots
                WHERE competition_id = ? AND season_id = ?
            )
            """,
            (competition_id, season_id),
        )
        for table in (
            "players",
            "matches",
            "odds_market_snapshots",
            "odds_market_consensus",
            "predictions",
            "notification_deliveries",
            "pre_match_snapshots",
            "model_training_runs",
            "model_releases",
        ):
            if table in existing_tables:
                connection.execute(
                f"""
                DELETE FROM {table}
                WHERE competition_id <> ? OR season_id <> ?
                """,
                (competition_id, season_id),
            )
        if "competition_participants" in existing_tables:
            connection.execute(
                "DELETE FROM competition_participants WHERE season_id <> ?",
                (season_id,),
            )
        if "seasons" in existing_tables:
            connection.execute(
                "DELETE FROM seasons WHERE season_id <> ?",
                (season_id,),
            )
        if "competitions" in existing_tables:
            connection.execute(
                "DELETE FROM competitions WHERE competition_id <> ?",
                (competition_id,),
            )


def export_public_bundle(
    output_dir: Path | None = None,
    source_db_path: Path | None = None,
    include_archive: bool = True,
    competition_context: CompetitionContext | None = None,
) -> BundleSummary:
    settings = get_settings()
    context = competition_context or resolve_competition_context()
    output_dir = output_dir or (
        settings.bundles_dir / "public_bundle"
        if context.is_default
        else settings.bundles_dir / context.namespace / "public_bundle"
    )
    source_db_path = source_db_path or settings.db_path
    output_dir.mkdir(parents=True, exist_ok=True)

    public_db_path = output_dir / PUBLIC_DB_NAME
    shutil.copy2(source_db_path, public_db_path)

    # Open the copied database through the normal schema initializer so
    # legacy exports receive scope columns before we trim to one competition.
    connection = get_connection(public_db_path)
    _scope_public_database(connection, context)
    with connection:
        connection.execute("DELETE FROM api_cache")
        connection.execute("DELETE FROM api_usage")
        notification_table = connection.execute(
            """
            SELECT 1
            FROM sqlite_master
            WHERE type = 'table' AND name = 'notification_deliveries'
            """
        ).fetchone()
        if notification_table:
            connection.execute("DELETE FROM notification_deliveries")
    connection.execute("VACUUM")

    processed_dir = output_dir / "processed"
    processed_dir.mkdir(parents=True, exist_ok=True)
    copied_files: list[Path] = []
    for file_name in ("calendar.csv", "rosters.csv"):
        source = context.processed_path(settings, file_name)
        if source.exists():
            destination = processed_dir / file_name
            shutil.copy2(source, destination)
            copied_files.append(destination)

    manifest = _public_manifest(connection, context)
    manifest_path = output_dir / MANIFEST_NAME
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    connection.close()

    archive_path: Path | None = None
    if include_archive:
        archive_base = (
            settings.bundles_dir / "quiniela_public_bundle"
            if context.is_default
            else settings.bundles_dir
            / context.namespace
            / f"quiniela_public_bundle_{context.namespace}"
        )
        archive_base.parent.mkdir(parents=True, exist_ok=True)
        archive_path = Path(shutil.make_archive(str(archive_base), "zip", root_dir=output_dir))

    sanitized_connection = sqlite3.connect(public_db_path)
    counts = _table_counts(sanitized_connection)
    sanitized_connection.close()
    return BundleSummary(
        bundle_dir=output_dir,
        archive_path=archive_path,
        manifest_path=manifest_path,
        db_path=public_db_path,
        copied_processed_files=copied_files,
        table_counts=counts,
    )


def import_public_bundle(
    bundle_path: Path,
    destination_db_path: Path | None = None,
    competition_context: CompetitionContext | None = None,
) -> BundleSummary:
    settings = get_settings()
    context = competition_context or resolve_competition_context()
    destination_db_path = destination_db_path or settings.db_path

    if bundle_path.is_file() and bundle_path.suffix.lower() == ".zip":
        temp_dir = Path(tempfile.mkdtemp(prefix="quiniela_public_bundle_"))
        with zipfile.ZipFile(bundle_path, "r") as zip_file:
            zip_file.extractall(temp_dir)
        bundle_dir = temp_dir
    else:
        bundle_dir = bundle_path

    manifest_path = bundle_dir / MANIFEST_NAME
    public_db_path = bundle_dir / PUBLIC_DB_NAME
    if not manifest_path.exists():
        raise FileNotFoundError(f"No se encontró {MANIFEST_NAME} en {bundle_dir}")
    if not public_db_path.exists():
        raise FileNotFoundError(f"No se encontró {PUBLIC_DB_NAME} en {bundle_dir}")

    destination_db_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(public_db_path, destination_db_path)

    copied_files: list[Path] = []
    for file_name in ("calendar.csv", "rosters.csv"):
        source = bundle_dir / "processed" / file_name
        if source.exists():
            destination = context.processed_path(settings, file_name)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            copied_files.append(destination)

    connection = sqlite3.connect(destination_db_path)
    counts = _table_counts(connection)
    connection.close()
    return BundleSummary(
        bundle_dir=bundle_dir,
        archive_path=bundle_path if bundle_path.is_file() else None,
        manifest_path=manifest_path,
        db_path=destination_db_path,
        copied_processed_files=copied_files,
        table_counts=counts,
    )


def public_bundle_status(
    db_path: Path | None = None,
    competition_context: CompetitionContext | None = None,
) -> dict[str, object]:
    context = competition_context or resolve_competition_context()
    connection = get_connection(db_path)
    counts = _table_counts(connection)
    matches_df = fetch_dataframe(
        connection,
        """
        SELECT match_id, date_cdmx, home_team, away_team, api_fixture_id
        FROM matches
        WHERE competition_id = ?
          AND season_id = ?
        ORDER BY datetime_cdmx
        """,
        (context.competition_id, context.season_id),
    )
    return {
        "competition_id": context.competition_id,
        "season_id": context.season_id,
        "namespace": context.namespace,
        "table_counts": counts,
        "matches": matches_df.to_dict(orient="records"),
    }
