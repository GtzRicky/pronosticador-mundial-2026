from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd
import typer
from rich.console import Console
from rich.table import Table

from quiniela.calendar_parser import ingest_calendar
from quiniela.config import get_settings
from quiniela.db import create_schema, fetch_dataframe, get_connection, seed_from_processed
from quiniela.historical_loader import backfill_team_history, fetch_today_data, update_after_match
from quiniela.html_report import build_predictions_html_report
from quiniela.matchday import MatchdayRunner
from quiniela.notifications import dispatch_notifications, test_notifications
from quiniela.outcome_model import train_outcome_model
from quiniela.output_manager import (
    cleanup_obsolete_outputs,
    evaluate_predictions,
    rebuild_outputs,
    write_automation_status,
)
from quiniela.player_model import train_player_model
from quiniela.player_evidence import (
    activate_model_release,
    build_player_targets,
    capture_pre_match_snapshot,
    evaluate_model_release,
    reconstruct_historical_snapshots,
    train_player_evidence,
)
from quiniela.predictor import Predictor, format_prediction_lines
from quiniela.public_bundle import export_public_bundle, import_public_bundle, public_bundle_status
from quiniela.roster_parser import ingest_rosters
from quiniela.web_lineup_fallback import refresh_web_lineup_fallback


app = typer.Typer(no_args_is_help=True)
console = Console()


@app.command("ingest-calendar")
def ingest_calendar_command() -> None:
    df = ingest_calendar()
    console.print(f"Calendario procesado: {len(df)} partidos -> data/processed/calendar.csv")


@app.command("ingest-rosters")
def ingest_rosters_command() -> None:
    df = ingest_rosters()
    console.print(f"Convocados procesados: {len(df)} jugadores -> data/processed/rosters.csv")


@app.command("init-db")
def init_db_command() -> None:
    settings = get_settings()
    calendar_path = settings.processed_dir / "calendar.csv"
    rosters_path = settings.processed_dir / "rosters.csv"
    if not calendar_path.exists():
        ingest_calendar()
    if not rosters_path.exists():
        ingest_rosters()

    connection = get_connection(settings.db_path)
    create_schema(connection)
    summary = seed_from_processed(connection, calendar_path, rosters_path)
    console.print(f"Base inicializada en {settings.db_path}")
    console.print(summary)


@app.command("fetch-history")
def fetch_history_command(
    teams: str = typer.Option(..., help="Lista separada por comas"),
    months: int = typer.Option(6, min=1, max=24),
    max_matches: int = typer.Option(8, min=1, max=20),
    dry_run: bool = typer.Option(False),
    force_refresh: bool = typer.Option(False, help="Ignora cache local y consulta API en vivo"),
) -> None:
    team_names = [team.strip() for team in teams.split(",") if team.strip()]
    report = backfill_team_history(
        team_names,
        months=months,
        max_matches_per_team=max_matches,
        dry_run=dry_run,
        force_refresh=force_refresh,
    )
    console.print(report)


@app.command("fetch-today")
def fetch_today_command(
    date: str = typer.Option(..., help="Fecha YYYY-MM-DD"),
    lineups_only: bool = typer.Option(False, "--lineups-only"),
    mode: str = typer.Option(
        "full",
        help="Modo: full, hourly, pre_match o lineups.",
    ),
    dry_run: bool = typer.Option(False),
    force_refresh: bool = typer.Option(False, help="Ignora cache local y consulta API en vivo"),
) -> None:
    result = fetch_today_data(
        date,
        lineups_only=lineups_only,
        dry_run=dry_run,
        force_refresh=force_refresh,
        fetch_mode=mode,
    )
    console.print(result)


@app.command("predict")
def predict_command(
    date: Optional[str] = typer.Option(None),
    home: Optional[str] = typer.Option(None),
    away: Optional[str] = typer.Option(None),
) -> None:
    predictor = Predictor()
    if date:
        predictions_df, output_path = predictor.predict_by_date(
            date,
            prediction_context="manual",
        )
        outputs = rebuild_outputs(connection=predictor.connection)
        for line in format_prediction_lines(predictions_df):
            console.print(line)
        console.print(f"CSV actualizado en {output_path}")
        console.print(outputs)
        return

    if home and away:
        predictions_df = predictor.predict_match(home, away)
        for line in format_prediction_lines(predictions_df):
            console.print(line)
        return

    raise typer.BadParameter("Debes enviar --date o bien --home y --away.")


@app.command("update-after-match")
def update_after_match_command(
    home: str = typer.Option(...),
    away: str = typer.Option(...),
    dry_run: bool = typer.Option(False),
    force_refresh: bool = typer.Option(False, help="Ignora cache local y consulta API en vivo"),
) -> None:
    result = update_after_match(home, away, dry_run=dry_run, force_refresh=force_refresh)
    console.print(result)
    if result.get("updated"):
        console.print(rebuild_outputs())


@app.command("report-api-usage")
def report_api_usage_command(date: Optional[str] = typer.Option(None)) -> None:
    settings = get_settings()
    connection = get_connection(settings.db_path)
    params = ()
    query = """
        SELECT request_date, endpoint, params_hash, cache_hit, status_code, created_at
        FROM api_usage
    """
    if date:
        query += " WHERE request_date = ?"
        params = (date,)
    query += " ORDER BY created_at DESC"

    usage_df = fetch_dataframe(connection, query, params)
    output_path = settings.logs_dir / "api_usage.csv"
    usage_df.to_csv(output_path, index=False, encoding="utf-8")

    table = Table(title="API Usage")
    for column in ["request_date", "endpoint", "cache_hit", "status_code", "created_at"]:
        table.add_column(column)
    for _, row in usage_df.head(20).iterrows():
        table.add_row(
            str(row.get("request_date")),
            str(row.get("endpoint")),
            str(row.get("cache_hit")),
            str(row.get("status_code")),
            str(row.get("created_at")),
        )
    console.print(table)
    console.print(f"CSV guardado en {output_path}")


@app.command("export-public-bundle")
def export_public_bundle_command(
    output_dir: Optional[str] = typer.Option(None, help="Directorio destino del bundle"),
    no_archive: bool = typer.Option(False, help="No genera ZIP, solo directorio"),
) -> None:
    bundle = export_public_bundle(
        output_dir=Path(output_dir) if output_dir else None,
        include_archive=not no_archive,
    )
    console.print(
        {
            "bundle_dir": str(bundle.bundle_dir),
            "archive_path": str(bundle.archive_path) if bundle.archive_path else None,
            "manifest_path": str(bundle.manifest_path),
            "db_path": str(bundle.db_path),
            "table_counts": bundle.table_counts,
        }
    )


@app.command("import-public-bundle")
def import_public_bundle_command(
    bundle_path: str = typer.Option(..., help="Ruta al directorio o ZIP del bundle"),
) -> None:
    bundle = import_public_bundle(Path(bundle_path))
    console.print(
        {
            "imported_db_path": str(bundle.db_path),
            "manifest_path": str(bundle.manifest_path),
            "copied_processed_files": [str(path) for path in bundle.copied_processed_files],
            "table_counts": bundle.table_counts,
        }
    )


@app.command("public-bundle-status")
def public_bundle_status_command() -> None:
    console.print(public_bundle_status())


@app.command("render-html-report")
def render_html_report_command(
    date: list[str] = typer.Option(..., help="Fecha YYYY-MM-DD. Repite la opción para múltiples fechas."),
    output: Optional[str] = typer.Option(None, help="Ruta opcional del archivo HTML de salida"),
) -> None:
    if output:
        output_path = build_predictions_html_report(date, Path(output))
        console.print(f"HTML guardado en {output_path}")
    else:
        console.print(rebuild_outputs())


@app.command("rebuild-outputs")
def rebuild_outputs_command() -> None:
    console.print(rebuild_outputs())


@app.command("evaluate-predictions")
def evaluate_predictions_command(
    match_id: Optional[list[str]] = typer.Option(None),
) -> None:
    settings = get_settings()
    connection = get_connection(settings.db_path)
    console.print(evaluate_predictions(connection, match_id or None))


@app.command("cleanup-obsolete-outputs")
def cleanup_obsolete_outputs_command(
    apply: bool = typer.Option(False, "--apply/--dry-run"),
) -> None:
    console.print(cleanup_obsolete_outputs(apply=apply))


@app.command("train-player-model")
def train_player_model_command(
    min_matches: int = typer.Option(30, min=5, help="Mínimo de partidos con cobertura suficiente"),
) -> None:
    settings = get_settings()
    connection = get_connection(settings.db_path)
    summary = train_player_model(connection, settings.model_artifacts_dir / "poisson_player_v1.pkl", min_matches=min_matches)
    console.print(summary)


@app.command("train-outcome-model")
def train_outcome_model_command(
    min_matches: int = typer.Option(40, min=20),
    min_per_class: int = typer.Option(8, min=3),
) -> None:
    settings = get_settings()
    connection = get_connection(settings.db_path)
    summary = train_outcome_model(
        connection,
        settings.model_artifacts_dir / "logit_outcome_v1.pkl",
        min_matches=min_matches,
        min_per_class=min_per_class,
    )
    console.print(summary)
    if summary.get("trained"):
        console.print(rebuild_outputs(connection=connection))


@app.command("capture-pre-match-snapshot")
def capture_pre_match_snapshot_command(
    match_id: str = typer.Option(...),
    window: str = typer.Option("manual"),
    source_kind: str = typer.Option("live"),
) -> None:
    settings = get_settings()
    connection = get_connection(settings.db_path)
    console.print(
        capture_pre_match_snapshot(
            match_id,
            window_label=window,
            connection=connection,
            source_kind=source_kind,
        )
    )


@app.command("build-player-targets")
def build_player_targets_command(
    match_id: Optional[list[str]] = typer.Option(None),
) -> None:
    settings = get_settings()
    connection = get_connection(settings.db_path)
    console.print(build_player_targets(connection, match_id or None))


@app.command("train-player-evidence")
def train_player_evidence_command(
    min_matches: int = typer.Option(30, min=10),
    min_new_matches: int = typer.Option(5, min=1),
    reconstruct_history: bool = typer.Option(
        False,
        "--reconstruct-history/--no-reconstruct-history",
    ),
    auto_promote: bool = typer.Option(
        True,
        "--auto-promote/--no-auto-promote",
    ),
) -> None:
    settings = get_settings()
    connection = get_connection(settings.db_path)
    reconstruction = None
    if reconstruct_history:
        reconstruction = reconstruct_historical_snapshots(connection)
        build_player_targets(connection)
    summary = train_player_evidence(
        connection,
        min_matches=min_matches,
        min_new_matches=min_new_matches,
        auto_promote=auto_promote,
    )
    console.print({"reconstruction": reconstruction, "training": summary})


@app.command("evaluate-model-release")
def evaluate_model_release_command(
    release_id: str = typer.Option(...),
) -> None:
    settings = get_settings()
    connection = get_connection(settings.db_path)
    console.print(evaluate_model_release(connection, release_id))


@app.command("activate-model-release")
def activate_model_release_command(
    release_id: str = typer.Option(...),
) -> None:
    settings = get_settings()
    connection = get_connection(settings.db_path)
    result = activate_model_release(connection, release_id)
    console.print(result)
    if result.get("activated"):
        console.print(rebuild_outputs(connection=connection))


@app.command("run-matchday")
def run_matchday_command(
    now: Optional[str] = typer.Option(
        None,
        help="Fecha/hora ISO opcional para pruebas reproducibles.",
    ),
) -> None:
    parsed_now = datetime.fromisoformat(now) if now else None
    console.print(MatchdayRunner().run(parsed_now))


@app.command("dispatch-notifications")
def dispatch_notifications_command(
    now: Optional[str] = typer.Option(
        None,
        help="Fecha/hora ISO opcional para pruebas reproducibles.",
    ),
) -> None:
    settings = get_settings()
    connection = get_connection(settings.db_path)
    parsed_now = datetime.fromisoformat(now) if now else None
    result = dispatch_notifications(
        connection=connection,
        now=parsed_now,
        settings=settings,
    )
    write_automation_status(connection)
    console.print(result)


@app.command("test-notifications")
def test_notifications_command(
    channel: str = typer.Option(
        "all",
        help="Canal a probar: ntfy, discord o all.",
    ),
) -> None:
    if channel not in {"ntfy", "discord", "all"}:
        raise typer.BadParameter("El canal debe ser ntfy, discord o all.")
    console.print(test_notifications(channel=channel))


@app.command("fetch-web-lineup-fallback")
def fetch_web_lineup_fallback_command(
    match_id: str = typer.Option(...),
    window: str = typer.Option("manual"),
    force: bool = typer.Option(False),
) -> None:
    settings = get_settings()
    connection = get_connection(settings.db_path)
    console.print(
        refresh_web_lineup_fallback(
            match_id,
            window_label=window,
            connection=connection,
            force=force,
        )
    )
