from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from typing import Optional

import pandas as pd
import typer
from rich.console import Console
from rich.table import Table

from quiniela.adapters.outbound.sqlite import (
    SQLiteDoctorHealthRepository,
    SQLiteOddsConsensusGateway,
)
from quiniela.application.use_cases import BuildOddsConsensusCommand
from quiniela.application.use_cases import BuildOddsConsensusUseCase
from quiniela.application.use_cases import DoctorUseCase
from quiniela.application.use_cases import GeneratePredictionCommand, GeneratePredictionUseCase
from quiniela.application.use_cases import RefreshMatchdayCommand, RefreshMatchdayUseCase
from quiniela.calendar_parser import ingest_calendar
from quiniela.config import get_settings
from quiniela.db import (
    create_schema,
    ensure_competition_scope,
    fetch_dataframe,
    get_connection,
    recover_stale_automation_runs,
    seed_from_processed,
)
from quiniela.historical_loader import backfill_team_history, fetch_today_data, update_after_match
from quiniela.html_report import build_predictions_html_report
from quiniela.matchday import MatchdayRunner
from quiniela.notifications import (
    dispatch_notifications,
    dry_run_notifications,
    explain_notification,
    notification_status,
    retry_failed_notifications,
    run_notification_cycle,
    send_isolated_lineup_test_notifications,
    test_notifications,
)
from quiniela.odds_loader import fetch_odds_by_date
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
from quiniela.infrastructure.competition_config import (
    CompetitionConfigError,
    CompetitionContext,
    resolve_competition_context,
)
from quiniela.web_lineup_fallback import refresh_web_lineup_fallback


app = typer.Typer(no_args_is_help=True)
console = Console()


def _competition_context(competition: str, season: Optional[str]) -> CompetitionContext:
    try:
        return resolve_competition_context(competition, season=season)
    except CompetitionConfigError as exc:
        raise typer.BadParameter(str(exc)) from exc


def _ingest_source(context: CompetitionContext, source_name: str) -> tuple[Path, Path]:
    source = context.config.data_sources.get(source_name)
    if source is None:
        raise typer.BadParameter(f"La configuracion no declara data_sources.{source_name}.")
    supported = {
        "calendar": "world_cup_markdown",
        "rosters": "national_team_markdown",
    }
    if source.parser != supported[source_name]:
        raise typer.BadParameter(
            f"Parser no implementado para {source_name}: {source.parser}. "
            "Proporciona un adaptador antes de ingerir esta competencia."
        )
    return source.input_path, context.processed_path(get_settings(), f"{source_name}.csv")


@app.command("doctor")
def doctor_command(
    output_format: str = typer.Option(
        "human",
        "--format",
        help="Formato de salida: human, json o markdown.",
    ),
    competition: str = typer.Option("world_cup_2026", "--competition"),
    season: Optional[str] = typer.Option(None, "--season"),
) -> None:
    context = _competition_context(competition, season)
    settings = get_settings()
    result = DoctorUseCase(
        settings=settings,
        competition_context=context,
        health_repository=SQLiteDoctorHealthRepository(settings.db_path),
    ).execute()
    if output_format == "human":
        console.print(result.to_human_text())
        return
    if output_format == "json":
        typer.echo(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return
    if output_format == "markdown":
        typer.echo(result.to_markdown())
        return
    raise typer.BadParameter("Formato invalido: usa human, json o markdown.")


@app.command("ingest-calendar")
def ingest_calendar_command(
    competition: str = typer.Option("world_cup_2026", "--competition"),
    season: Optional[str] = typer.Option(None, "--season"),
) -> None:
    context = _competition_context(competition, season)
    input_path, output_path = _ingest_source(context, "calendar")
    df = ingest_calendar(input_path, output_path)
    console.print(f"Calendario procesado: {len(df)} partidos -> {output_path}")


@app.command("ingest-rosters")
def ingest_rosters_command(
    competition: str = typer.Option("world_cup_2026", "--competition"),
    season: Optional[str] = typer.Option(None, "--season"),
) -> None:
    context = _competition_context(competition, season)
    input_path, output_path = _ingest_source(context, "rosters")
    df = ingest_rosters(input_path, output_path)
    console.print(f"Convocados procesados: {len(df)} jugadores -> {output_path}")


@app.command("init-db")
def init_db_command(
    competition: str = typer.Option("world_cup_2026", "--competition"),
    season: Optional[str] = typer.Option(None, "--season"),
) -> None:
    settings = get_settings()
    context = _competition_context(competition, season)
    calendar_path = context.processed_path(settings, "calendar.csv")
    rosters_path = context.processed_path(settings, "rosters.csv")
    if not calendar_path.exists():
        input_path, _ = _ingest_source(context, "calendar")
        ingest_calendar(input_path, calendar_path)
    if not rosters_path.exists():
        input_path, _ = _ingest_source(context, "rosters")
        ingest_rosters(input_path, rosters_path)

    connection = get_connection(settings.db_path)
    create_schema(connection)
    ensure_competition_scope(connection, context)
    summary = seed_from_processed(connection, calendar_path, rosters_path, context=context)
    console.print(f"Base inicializada en {settings.db_path}")
    console.print(summary)


@app.command("fetch-history")
def fetch_history_command(
    teams: str = typer.Option(..., help="Lista separada por comas"),
    months: int = typer.Option(6, min=1, max=24),
    max_matches: int = typer.Option(8, min=1, max=20),
    dry_run: bool = typer.Option(False),
    force_refresh: bool = typer.Option(False, help="Ignora cache local y consulta API en vivo"),
    competition: str = typer.Option("world_cup_2026", "--competition"),
    season: Optional[str] = typer.Option(None, "--season"),
) -> None:
    context = _competition_context(competition, season)
    team_names = [team.strip() for team in teams.split(",") if team.strip()]
    report = backfill_team_history(
        team_names,
        months=months,
        max_matches_per_team=max_matches,
        dry_run=dry_run,
        force_refresh=force_refresh,
        competition_context=context,
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
    competition: str = typer.Option("world_cup_2026", "--competition"),
    season: Optional[str] = typer.Option(None, "--season"),
) -> None:
    context = _competition_context(competition, season)
    result = fetch_today_data(
        date,
        lineups_only=lineups_only,
        dry_run=dry_run,
        force_refresh=force_refresh,
        fetch_mode=mode,
        competition_context=context,
    )
    console.print(result)


@app.command("predict")
def predict_command(
    date: Optional[str] = typer.Option(None),
    home: Optional[str] = typer.Option(None),
    away: Optional[str] = typer.Option(None),
    competition: str = typer.Option("world_cup_2026", "--competition"),
    season: Optional[str] = typer.Option(None, "--season"),
) -> None:
    context = _competition_context(competition, season)
    predictor = Predictor(competition_context=context)
    use_case = GeneratePredictionUseCase(predictor=predictor)
    if date:
        result = use_case.execute(
            GeneratePredictionCommand(date=date, prediction_context="manual")
        )
        predictions_df = result.predictions
        output_path = result.output_path
        outputs = rebuild_outputs(connection=predictor.connection, competition_context=context)
        for line in format_prediction_lines(predictions_df):
            console.print(line)
        console.print(f"CSV actualizado en {output_path}")
        console.print(outputs)
        return

    if home and away:
        result = use_case.execute(GeneratePredictionCommand(home=home, away=away))
        predictions_df = result.predictions
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
    competition: str = typer.Option("world_cup_2026", "--competition"),
    season: Optional[str] = typer.Option(None, "--season"),
) -> None:
    context = _competition_context(competition, season)
    result = update_after_match(
        home,
        away,
        dry_run=dry_run,
        force_refresh=force_refresh,
        competition_context=context,
    )
    console.print(result)
    if result.get("updated"):
        console.print(rebuild_outputs(competition_context=context))


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
    competition: str = typer.Option("world_cup_2026", "--competition"),
    season: Optional[str] = typer.Option(None, "--season"),
) -> None:
    context = _competition_context(competition, season)
    bundle = export_public_bundle(
        output_dir=Path(output_dir) if output_dir else None,
        include_archive=not no_archive,
        competition_context=context,
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
    competition: str = typer.Option("world_cup_2026", "--competition"),
    season: Optional[str] = typer.Option(None, "--season"),
) -> None:
    context = _competition_context(competition, season)
    bundle = import_public_bundle(Path(bundle_path), competition_context=context)
    console.print(
        {
            "imported_db_path": str(bundle.db_path),
            "manifest_path": str(bundle.manifest_path),
            "copied_processed_files": [str(path) for path in bundle.copied_processed_files],
            "table_counts": bundle.table_counts,
        }
    )


@app.command("public-bundle-status")
def public_bundle_status_command(
    competition: str = typer.Option("world_cup_2026", "--competition"),
    season: Optional[str] = typer.Option(None, "--season"),
) -> None:
    context = _competition_context(competition, season)
    console.print(public_bundle_status(competition_context=context))


@app.command("render-html-report")
def render_html_report_command(
    date: list[str] = typer.Option(..., help="Fecha YYYY-MM-DD. Repite la opción para múltiples fechas."),
    output: Optional[str] = typer.Option(None, help="Ruta opcional del archivo HTML de salida"),
    competition: str = typer.Option("world_cup_2026", "--competition"),
    season: Optional[str] = typer.Option(None, "--season"),
) -> None:
    context = _competition_context(competition, season)
    if output:
        output_path = build_predictions_html_report(
            date,
            Path(output),
            competition_context=context,
        )
        console.print(f"HTML guardado en {output_path}")
    else:
        console.print(rebuild_outputs(competition_context=context))


@app.command("rebuild-outputs")
def rebuild_outputs_command(
    competition: str = typer.Option("world_cup_2026", "--competition"),
    season: Optional[str] = typer.Option(None, "--season"),
) -> None:
    context = _competition_context(competition, season)
    console.print(rebuild_outputs(competition_context=context))


@app.command("evaluate-predictions")
def evaluate_predictions_command(
    match_id: Optional[list[str]] = typer.Option(None),
    competition: str = typer.Option("world_cup_2026", "--competition"),
    season: Optional[str] = typer.Option(None, "--season"),
) -> None:
    context = _competition_context(competition, season)
    settings = get_settings()
    connection = get_connection(settings.db_path)
    console.print(
        evaluate_predictions(
            connection,
            match_id or None,
            competition_context=context,
        )
    )


@app.command("cleanup-obsolete-outputs")
def cleanup_obsolete_outputs_command(
    apply: bool = typer.Option(False, "--apply/--dry-run"),
    competition: str = typer.Option("world_cup_2026", "--competition"),
    season: Optional[str] = typer.Option(None, "--season"),
) -> None:
    context = _competition_context(competition, season)
    console.print(cleanup_obsolete_outputs(apply=apply, competition_context=context))


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
    competition: str = typer.Option("world_cup_2026", "--competition"),
    season: Optional[str] = typer.Option(None, "--season"),
) -> None:
    context = _competition_context(competition, season)
    settings = get_settings()
    connection = get_connection(settings.db_path)
    console.print(
        capture_pre_match_snapshot(
            match_id,
            window_label=window,
            connection=connection,
            source_kind=source_kind,
            competition_context=context,
        )
    )


@app.command("build-player-targets")
def build_player_targets_command(
    match_id: Optional[list[str]] = typer.Option(None),
    competition: str = typer.Option("world_cup_2026", "--competition"),
    season: Optional[str] = typer.Option(None, "--season"),
) -> None:
    context = _competition_context(competition, season)
    settings = get_settings()
    connection = get_connection(settings.db_path)
    console.print(
        build_player_targets(
            connection,
            match_id or None,
            competition_context=context,
        )
    )


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
    competition: str = typer.Option("world_cup_2026", "--competition"),
    season: Optional[str] = typer.Option(None, "--season"),
) -> None:
    context = _competition_context(competition, season)
    parsed_now = datetime.fromisoformat(now) if now else None
    result = RefreshMatchdayUseCase(runner=MatchdayRunner(competition_context=context)).execute(
        RefreshMatchdayCommand(now=parsed_now)
    )
    console.print(result.summary)


@app.command("recover-automation-runs")
def recover_automation_runs_command(
    stale_after_minutes: int = typer.Option(25, min=1),
    apply: bool = typer.Option(False, "--apply/--dry-run"),
    competition: str = typer.Option("world_cup_2026", "--competition"),
    season: Optional[str] = typer.Option(None, "--season"),
) -> None:
    context = _competition_context(competition, season)
    settings = get_settings()
    connection = get_connection(settings.db_path)
    result = recover_stale_automation_runs(
        connection,
        stale_after_minutes=stale_after_minutes,
        apply=apply,
    )
    write_automation_status(connection, competition_context=context)
    console.print(result)


@app.command("run-notification-cycle")
def run_notification_cycle_command(
    now: Optional[str] = typer.Option(
        None,
        help="Fecha/hora ISO opcional para pruebas reproducibles.",
    ),
    competition: str = typer.Option("world_cup_2026", "--competition"),
    season: Optional[str] = typer.Option(None, "--season"),
) -> None:
    context = _competition_context(competition, season)
    settings = get_settings()
    connection = get_connection(settings.db_path)
    parsed_now = datetime.fromisoformat(now) if now else None
    result = run_notification_cycle(
        connection=connection,
        now=parsed_now,
        settings=settings,
        competition_context=context,
    )
    write_automation_status(connection, competition_context=context)
    console.print(result)


@app.command("dispatch-notifications")
def dispatch_notifications_command(
    now: Optional[str] = typer.Option(
        None,
        help="Fecha/hora ISO opcional para pruebas reproducibles.",
    ),
    competition: str = typer.Option("world_cup_2026", "--competition"),
    season: Optional[str] = typer.Option(None, "--season"),
) -> None:
    context = _competition_context(competition, season)
    settings = get_settings()
    connection = get_connection(settings.db_path)
    parsed_now = datetime.fromisoformat(now) if now else None
    result = dispatch_notifications(
        connection=connection,
        now=parsed_now,
        settings=settings,
        competition_context=context,
    )
    write_automation_status(connection, competition_context=context)
    console.print(result)


@app.command("notifications-status")
def notifications_status_command(
    recent: int = typer.Option(10, min=0, max=100, help="Numero de entregas recientes a mostrar."),
    competition: str = typer.Option("world_cup_2026", "--competition"),
    season: Optional[str] = typer.Option(None, "--season"),
) -> None:
    context = _competition_context(competition, season)
    settings = get_settings()
    connection = get_connection(settings.db_path)
    console.print(
        notification_status(
            connection,
            settings=settings,
            include_recent=recent,
            competition_context=context,
        )
    )


@app.command("retry-failed-notifications")
def retry_failed_notifications_command(
    apply: bool = typer.Option(False, "--apply/--dry-run"),
    now: Optional[str] = typer.Option(None, help="Fecha/hora ISO opcional para pruebas reproducibles."),
    competition: str = typer.Option("world_cup_2026", "--competition"),
    season: Optional[str] = typer.Option(None, "--season"),
) -> None:
    context = _competition_context(competition, season)
    settings = get_settings()
    connection = get_connection(settings.db_path)
    parsed_now = datetime.fromisoformat(now) if now else None
    console.print(
        retry_failed_notifications(
            connection,
            now=parsed_now,
            settings=settings,
            apply=apply,
            competition_context=context,
        )
    )


@app.command("dry-run-notifications")
def dry_run_notifications_command(
    now: Optional[str] = typer.Option(None, help="Fecha/hora ISO opcional para pruebas reproducibles."),
    competition: str = typer.Option("world_cup_2026", "--competition"),
    season: Optional[str] = typer.Option(None, "--season"),
) -> None:
    context = _competition_context(competition, season)
    settings = get_settings()
    connection = get_connection(settings.db_path)
    parsed_now = datetime.fromisoformat(now) if now else None
    console.print(
        dry_run_notifications(
            connection,
            now=parsed_now,
            settings=settings,
            competition_context=context,
        )
    )


@app.command("explain-notification")
def explain_notification_command(
    notification_id: int = typer.Option(..., "--id", min=1),
    competition: str = typer.Option("world_cup_2026", "--competition"),
    season: Optional[str] = typer.Option(None, "--season"),
) -> None:
    context = _competition_context(competition, season)
    settings = get_settings()
    connection = get_connection(settings.db_path)
    console.print(
        explain_notification(
            notification_id,
            connection,
            settings=settings,
            competition_context=context,
        )
    )


@app.command("fetch-odds")
def fetch_odds_command(
    date: str = typer.Option(..., help="Fecha YYYY-MM-DD"),
    dry_run: bool = typer.Option(False),
    force_refresh: bool = typer.Option(False),
    competition: str = typer.Option("world_cup_2026", "--competition"),
    season: Optional[str] = typer.Option(None, "--season"),
) -> None:
    context = _competition_context(competition, season)
    console.print(
        fetch_odds_by_date(
            date,
            dry_run=dry_run,
            force_refresh=force_refresh,
            competition_context=context,
        )
    )


@app.command("build-odds-consensus")
def build_odds_consensus_command(
    date: Optional[str] = typer.Option(None, help="Fecha YYYY-MM-DD"),
    fixture_id: Optional[str] = typer.Option(None),
    competition: str = typer.Option("world_cup_2026", "--competition"),
    season: Optional[str] = typer.Option(None, "--season"),
) -> None:
    context = _competition_context(competition, season)
    settings = get_settings()
    connection = get_connection(settings.db_path)
    use_case = BuildOddsConsensusUseCase(
        gateway=SQLiteOddsConsensusGateway(connection, context),
    )
    console.print(
        use_case.execute(
            BuildOddsConsensusCommand(date=date, fixture_id=fixture_id)
        ).as_dict()
    )


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


@app.command("send-lineup-test-notifications")
def send_lineup_test_notifications_command(
    date: str = typer.Option(..., help="Fecha local YYYY-MM-DD"),
    send: bool = typer.Option(False, "--send/--dry-run"),
    competition: str = typer.Option("world_cup_2026", "--competition"),
    season: Optional[str] = typer.Option(None, "--season"),
) -> None:
    context = _competition_context(competition, season)
    settings = get_settings()
    connection = get_connection(settings.db_path)
    before_count = connection.execute(
        "SELECT COUNT(*) AS total FROM notification_deliveries"
    ).fetchone()["total"]
    result = send_isolated_lineup_test_notifications(
        date_str=date,
        connection=connection,
        settings=settings,
        send=send,
        competition_context=context,
    )
    after_count = connection.execute(
        "SELECT COUNT(*) AS total FROM notification_deliveries"
    ).fetchone()["total"]
    result["notification_deliveries_before"] = int(before_count)
    result["notification_deliveries_after"] = int(after_count)
    result["notification_deliveries_unchanged"] = before_count == after_count
    console.print(result)


@app.command("fetch-web-lineup-fallback")
def fetch_web_lineup_fallback_command(
    match_id: str = typer.Option(...),
    window: str = typer.Option("manual"),
    force: bool = typer.Option(False),
    competition: str = typer.Option("world_cup_2026", "--competition"),
    season: Optional[str] = typer.Option(None, "--season"),
) -> None:
    context = _competition_context(competition, season)
    settings = get_settings()
    connection = get_connection(settings.db_path)
    console.print(
        refresh_web_lineup_fallback(
            match_id,
            window_label=window,
            connection=connection,
            force=force,
            competition_context=context,
        )
    )


if __name__ == "__main__":
    app()
