from __future__ import annotations

from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import sqlite3
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from quiniela.config import get_settings
from quiniela.db import fetch_dataframe, get_connection
from quiniela.html_report import (
    _load_lineups_by_fixture,
    _load_prediction_impacts,
    render_predictions_html,
)
from quiniela.name_maps import normalize_team_name
from quiniela.outcome_model import load_outcome_model_artifact


STABLE_PREDICTION_FILES = {
    "index.html",
    "today.html",
    "predictions_latest.csv",
    "predictions_latest.json",
    "predictions_history.csv",
    "predictions_history.json",
}
STABLE_LOG_FILES = {
    "data_quality.md",
    "model_performance.md",
    "automation_status.md",
}


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, path)


def _atomic_write_dataframe(frame: pd.DataFrame, path: Path) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    frame.to_csv(temporary, index=False, encoding="utf-8")
    os.replace(temporary, path)


def _score_parts(value: Any) -> tuple[int | None, int | None]:
    try:
        home, away = str(value).split("-", 1)
        return int(home), int(away)
    except (TypeError, ValueError):
        return None, None


def _outcome(home: int, away: int) -> str:
    if home > away:
        return "home"
    if away > home:
        return "away"
    return "draw"


def _poisson_deviance(actual: float, predicted: float) -> float:
    predicted = max(float(predicted), 1e-9)
    actual = float(actual)
    if actual == 0:
        return 2.0 * predicted
    return 2.0 * (predicted - actual + actual * math.log(actual / predicted))


def _evaluation_metrics(row: sqlite3.Row) -> dict[str, Any]:
    predicted_home, predicted_away = _score_parts(row["predicted_score"])
    hybrid_home, hybrid_away = _score_parts(row["hybrid_predicted_score"])
    actual_home = int(row["actual_home_goals"])
    actual_away = int(row["actual_away_goals"])
    actual_outcome = _outcome(actual_home, actual_away)
    probabilities = {
        "home": float(row["home_win_probability"] or 0.0),
        "draw": float(row["draw_probability"] or 0.0),
        "away": float(row["away_win_probability"] or 0.0),
    }
    probability_total = sum(probabilities.values())
    if probability_total > 0:
        probabilities = {
            key: value / probability_total for key, value in probabilities.items()
        }
        actual_probability = max(probabilities[actual_outcome], 1e-15)
        log_loss = -math.log(actual_probability)
        brier = sum(
            (probability - float(label == actual_outcome)) ** 2
            for label, probability in probabilities.items()
        )
        calibration_error = abs(1.0 - actual_probability)
        predicted_outcome = max(probabilities, key=probabilities.get)
        outcome_correct = int(predicted_outcome == actual_outcome)
    else:
        log_loss = None
        brier = None
        calibration_error = None
        outcome_correct = None

    if predicted_home is None or predicted_away is None:
        goal_mae = None
        poisson_deviance = None
    else:
        goal_mae = (
            abs(actual_home - predicted_home) + abs(actual_away - predicted_away)
        ) / 2.0
        expected_home = float(row["lambda_home"] or predicted_home or 1e-9)
        expected_away = float(row["lambda_away"] or predicted_away or 1e-9)
        poisson_deviance = (
            _poisson_deviance(actual_home, expected_home)
            + _poisson_deviance(actual_away, expected_away)
        ) / 2.0
    return {
        "actual_outcome": actual_outcome,
        "predicted_home_goals": predicted_home,
        "predicted_away_goals": predicted_away,
        "hybrid_home_goals": hybrid_home,
        "hybrid_away_goals": hybrid_away,
        "goal_mae": goal_mae,
        "poisson_deviance": poisson_deviance,
        "outcome_correct": outcome_correct,
        "log_loss": log_loss,
        "brier_score": brier,
        "calibration_error": calibration_error,
    }


def _prediction_rows_for_evaluation(
    connection: sqlite3.Connection,
    match_ids: list[str] | None = None,
) -> list[sqlite3.Row]:
    where = ""
    params: list[Any] = []
    if match_ids:
        placeholders = ",".join("?" for _ in match_ids)
        where = f"AND p.match_id IN ({placeholders})"
        params.extend(match_ids)
    return connection.execute(
        f"""
        SELECT p.*, m.datetime_cdmx,
               ar.home_goals AS actual_home_goals,
               ar.away_goals AS actual_away_goals,
               json_extract(p.source_json, '$.lambda_home') AS lambda_home,
               json_extract(p.source_json, '$.lambda_away') AS lambda_away
        FROM predictions p
        INNER JOIN matches m ON m.match_id = p.match_id
        INNER JOIN actual_results ar ON ar.match_id = p.match_id
        WHERE p.is_pre_kickoff = 1
          AND julianday(p.generated_at_utc) < julianday(m.datetime_cdmx)
          {where}
        ORDER BY p.match_id, julianday(p.generated_at_utc), p.id
        """,
        params,
    ).fetchall()


def evaluate_predictions(
    connection: sqlite3.Connection | None = None,
    match_ids: list[str] | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    connection = connection or get_connection(settings.db_path)
    rows = _prediction_rows_for_evaluation(connection, match_ids)
    canonical_ids: dict[str, int] = {}
    for row in rows:
        canonical_ids[str(row["match_id"])] = int(row["id"])

    inserted = 0
    evaluated_at = datetime.now(timezone.utc).isoformat()
    with connection:
        for row in rows:
            metrics = _evaluation_metrics(row)
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO prediction_evaluations (
                    prediction_id, match_id, is_canonical,
                    actual_home_goals, actual_away_goals,
                    predicted_home_goals, predicted_away_goals,
                    hybrid_home_goals, hybrid_away_goals,
                    goal_mae, poisson_deviance, outcome_correct,
                    log_loss, brier_score, calibration_error,
                    metrics_json, evaluated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(row["id"]),
                    str(row["match_id"]),
                    int(canonical_ids[str(row["match_id"])] == int(row["id"])),
                    int(row["actual_home_goals"]),
                    int(row["actual_away_goals"]),
                    metrics["predicted_home_goals"],
                    metrics["predicted_away_goals"],
                    metrics["hybrid_home_goals"],
                    metrics["hybrid_away_goals"],
                    metrics["goal_mae"],
                    metrics["poisson_deviance"],
                    metrics["outcome_correct"],
                    metrics["log_loss"],
                    metrics["brier_score"],
                    metrics["calibration_error"],
                    json.dumps(metrics, ensure_ascii=False, sort_keys=True),
                    evaluated_at,
                ),
            )
            inserted += int(cursor.rowcount == 1)
        for match_id, prediction_id in canonical_ids.items():
            connection.execute(
                "UPDATE prediction_evaluations SET is_canonical = 0 WHERE match_id = ?",
                (match_id,),
            )
            connection.execute(
                "UPDATE prediction_evaluations SET is_canonical = 1 WHERE prediction_id = ?",
                (prediction_id,),
            )
    return {
        "evaluated_predictions": len(rows),
        "inserted": inserted,
        "canonical_matches": len(canonical_ids),
    }


def _history_frame(connection: sqlite3.Connection) -> pd.DataFrame:
    frame = fetch_dataframe(
        connection,
        """
        WITH canonical AS (
            SELECT match_id, prediction_id
            FROM prediction_evaluations
            WHERE is_canonical = 1
        )
        SELECT
            p.id AS prediction_id,
            p.match_id,
            m.date_cdmx,
            m.time_cdmx,
            m.datetime_cdmx,
            m.home_team,
            m.away_team,
            m.status,
            p.predicted_score,
            p.probability,
            p.hybrid_predicted_score,
            p.hybrid_probability,
            p.home_win_probability,
            p.draw_probability,
            p.away_win_probability,
            p.model_version,
            p.outcome_model_version,
            p.prediction_context,
            p.window_label,
            p.generated_at_utc,
            p.data_freshness_at,
            p.is_pre_kickoff,
            CASE WHEN c.prediction_id = p.id THEN 1 ELSE 0 END AS is_canonical,
            json_extract(p.source_json, '$.home_lineup_source') AS home_lineup_source,
            json_extract(p.source_json, '$.away_lineup_source') AS away_lineup_source,
            ar.home_goals AS actual_home_goals,
            ar.away_goals AS actual_away_goals,
            pe.goal_mae,
            pe.poisson_deviance,
            pe.outcome_correct,
            pe.log_loss,
            pe.brier_score,
            pe.calibration_error
        FROM predictions p
        INNER JOIN matches m ON m.match_id = p.match_id
        LEFT JOIN actual_results ar ON ar.match_id = p.match_id
        LEFT JOIN prediction_evaluations pe ON pe.prediction_id = p.id
        LEFT JOIN canonical c ON c.match_id = p.match_id
        ORDER BY m.datetime_cdmx, julianday(p.generated_at_utc), p.id
        """,
    )
    return frame


def _latest_frame(history: pd.DataFrame, now: datetime) -> pd.DataFrame:
    if history.empty:
        return history.copy()
    selected = []
    utc_now = pd.Timestamp(now)
    if utc_now.tzinfo is None:
        utc_now = utc_now.tz_localize(get_settings().local_timezone)
    utc_now = utc_now.tz_convert("UTC")
    for _, group in history.groupby("match_id", sort=False):
        kickoff = pd.Timestamp(group.iloc[0]["datetime_cdmx"])
        if kickoff.tzinfo is None:
            kickoff = kickoff.tz_localize(get_settings().local_timezone)
        canonical = group[group["is_canonical"] == 1]
        if utc_now >= kickoff.tz_convert("UTC") and not canonical.empty:
            selected.append(canonical.iloc[-1])
        else:
            selected.append(group.iloc[-1])
    return pd.DataFrame(selected).reset_index(drop=True)


def _json_records(frame: pd.DataFrame) -> str:
    return frame.to_json(
        orient="records",
        force_ascii=False,
        indent=2,
        date_format="iso",
    )


def _timeline_by_match(history: pd.DataFrame) -> dict[str, list[dict[str, Any]]]:
    timelines: dict[str, list[dict[str, Any]]] = {}
    if history.empty:
        return timelines
    for match_id, group in history.groupby("match_id", sort=False):
        pre_match = group[group["is_pre_kickoff"] == 1]
        timelines[str(match_id)] = pre_match.to_dict(orient="records")
    return timelines


def _html_rows(
    connection: sqlite3.Connection,
    latest: pd.DataFrame,
    timelines: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    if latest.empty:
        return []
    match_ids = latest["match_id"].astype(str).tolist()
    placeholders = ",".join("?" for _ in match_ids)
    metadata = fetch_dataframe(
        connection,
        f"""
        SELECT match_id, date_cdmx, time_cdmx, datetime_cdmx,
               home_team, away_team, home_team_norm, away_team_norm,
               group_name, stadium, stage, status, api_fixture_id
        FROM matches
        WHERE match_id IN ({placeholders})
        """,
        match_ids,
    ).set_index("match_id")
    rows: list[dict[str, Any]] = []
    for prediction in latest.to_dict(orient="records"):
        match = metadata.loc[prediction["match_id"]].to_dict()
        row = {**match, **prediction}
        row["timeline"] = timelines.get(str(prediction["match_id"]), [])
        rows.append(row)
    return sorted(rows, key=lambda row: (row["datetime_cdmx"], row["home_team"]))


def _fill_snapshot_impacts(
    connection: sqlite3.Connection,
    rows: list[dict[str, Any]],
    impacts: dict[tuple[str, str], list[dict[str, Any]]],
) -> None:
    for row in rows:
        match_id = str(row["match_id"])
        expected_teams = {
            normalize_team_name(str(row["home_team"])),
            normalize_team_name(str(row["away_team"])),
        }
        if all((match_id, team_norm) in impacts for team_norm in expected_teams):
            continue
        snapshot = connection.execute(
            """
            SELECT id
            FROM pre_match_snapshots
            WHERE match_id = ?
              AND julianday(captured_at) < julianday(kickoff_at)
            ORDER BY julianday(captured_at) DESC, id DESC
            LIMIT 1
            """,
            (match_id,),
        ).fetchone()
        if snapshot is None:
            continue
        players = connection.execute(
            """
            SELECT team_norm, features_json
            FROM pre_match_player_snapshots
            WHERE snapshot_id = ?
            ORDER BY net_impact DESC, id
            """,
            (int(snapshot["id"]),),
        ).fetchall()
        for player in players:
            key = (match_id, str(player["team_norm"]))
            impacts.setdefault(key, []).append(json.loads(player["features_json"]))


def _write_data_quality(connection: sqlite3.Connection, path: Path) -> None:
    row = connection.execute(
        """
        SELECT
            COUNT(*) AS matches,
            SUM(CASE WHEN api_fixture_id IS NOT NULL THEN 1 ELSE 0 END) AS fixtures,
            SUM(CASE WHEN ar.match_id IS NOT NULL THEN 1 ELSE 0 END) AS results
        FROM matches m
        LEFT JOIN actual_results ar ON ar.match_id = m.match_id
        """
    ).fetchone()
    unresolved = connection.execute(
        "SELECT COUNT(*) FROM players WHERE api_player_id IS NULL"
    ).fetchone()[0]
    complete_lineups = connection.execute(
        """
        SELECT COUNT(*) FROM historical_lineups
        WHERE json_array_length(json_extract(source_json, '$.startXI')) >= 11
        """
    ).fetchone()[0]
    text = "\n".join(
        [
            "# Data Quality",
            "",
            f"- Matches: {int(row['matches'] or 0)}",
            f"- Fixtures linked: {int(row['fixtures'] or 0)}",
            f"- Final results: {int(row['results'] or 0)}",
            f"- Complete team lineups: {int(complete_lineups or 0)}",
            f"- Players without API id: {int(unresolved or 0)}",
        ]
    )
    _atomic_write_text(path, text)


def _write_model_performance(connection: sqlite3.Connection, path: Path) -> None:
    aggregate = connection.execute(
        """
        SELECT COUNT(*) AS matches,
               AVG(goal_mae) AS goal_mae,
               AVG(poisson_deviance) AS poisson_deviance,
               AVG(log_loss) AS log_loss,
               AVG(brier_score) AS brier_score,
               AVG(calibration_error) AS calibration_error,
               AVG(outcome_correct) AS accuracy
        FROM prediction_evaluations
        WHERE is_canonical = 1
        """
    ).fetchone()
    release = connection.execute(
        """
        SELECT release_id, preliminary, activated_at
        FROM model_releases
        WHERE status = 'active'
        ORDER BY activated_at DESC, id DESC LIMIT 1
        """
    ).fetchone()
    player_evaluations = connection.execute(
        """
        SELECT source_kind, COUNT(DISTINCT match_id) AS matches,
               COUNT(*) AS players,
               AVG(participated) AS participation_rate
        FROM player_prediction_evaluations
        GROUP BY source_kind
        ORDER BY source_kind
        """
    ).fetchall()
    outcome_bundle = load_outcome_model_artifact() or {}
    outcome_metrics = outcome_bundle.get("metrics", {})
    baseline_metrics = outcome_bundle.get("frequency_baseline_metrics", {})
    lines = [
        "# Model Performance",
        "",
        f"- Evaluated matches: {int(aggregate['matches'] or 0)}",
        f"- Goal MAE: {float(aggregate['goal_mae'] or 0.0):.4f}",
        f"- Poisson deviance: {float(aggregate['poisson_deviance'] or 0.0):.4f}",
        f"- Outcome log-loss: {float(aggregate['log_loss'] or 0.0):.4f}",
        f"- Outcome Brier: {float(aggregate['brier_score'] or 0.0):.4f}",
        f"- Calibration error: {float(aggregate['calibration_error'] or 0.0):.4f}",
        f"- Outcome accuracy: {float(aggregate['accuracy'] or 0.0):.4f}",
        "",
        "## Active release",
        f"- Release: {release['release_id'] if release else 'v1 fallback'}",
        f"- Preliminary: {bool(release['preliminary']) if release else True}",
        f"- Activated at: {release['activated_at'] if release else 'n/a'}",
        "",
        "## Outcome Logit",
        f"- Model: {outcome_bundle.get('model_version', 'unavailable')}",
        f"- Training matches: {outcome_bundle.get('training_matches', 0)}",
        f"- Log-loss: {float(outcome_metrics.get('log_loss', 0.0)):.4f}",
        f"- Baseline log-loss: {float(baseline_metrics.get('log_loss', 0.0)):.4f}",
        f"- Brier: {float(outcome_metrics.get('brier_score', 0.0)):.4f}",
        f"- Calibration error: {float(outcome_metrics.get('calibration_error', 0.0)):.4f}",
        "",
        "## Player evidence coverage",
    ]
    lines.extend(
        (
            f"- {row['source_kind']}: {int(row['matches'])} matches, "
            f"{int(row['players'])} player evaluations, "
            f"{float(row['participation_rate'] or 0.0):.1%} participation"
        )
        for row in player_evaluations
    )
    if not player_evaluations:
        lines.append("- No player evaluations available.")
    _atomic_write_text(path, "\n".join(lines))


def _write_automation_status(connection: sqlite3.Connection, path: Path) -> None:
    runs = connection.execute(
        """
        SELECT run_key, action, status, scheduled_for, finished_at
        FROM automation_runs
        ORDER BY id DESC LIMIT 20
        """
    ).fetchall()
    usage = connection.execute(
        """
        SELECT COUNT(*) AS requests
        FROM api_usage
        WHERE request_date = date('now')
          AND cache_hit = 0
        """
    ).fetchone()
    pending = connection.execute(
        """
        SELECT COUNT(*) AS matches
        FROM matches m
        LEFT JOIN actual_results ar ON ar.match_id = m.match_id
        WHERE ar.match_id IS NULL
          AND m.status NOT IN ('PST', 'CANC', 'ABD', 'AWD', 'WO')
        """
    ).fetchone()
    freshness = connection.execute(
        """
        SELECT MAX(value) AS freshness
        FROM (
            SELECT MAX(generated_at_utc) AS value FROM predictions
            UNION ALL
            SELECT MAX(updated_at) AS value FROM actual_results
            UNION ALL
            SELECT MAX(fetched_at) AS value FROM historical_lineups
        )
        """
    ).fetchone()
    notification_counts = connection.execute(
        """
        SELECT
            SUM(CASE WHEN status IN ('pending', 'waiting_prediction', 'retry', 'sending')
                     THEN 1 ELSE 0 END) AS pending,
            SUM(CASE WHEN status = 'sent' THEN 1 ELSE 0 END) AS sent,
            SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed
        FROM notification_deliveries
        """
    ).fetchone()
    recent_notifications = connection.execute(
        """
        SELECT match_id, window_label, channel, status, error_code
        FROM notification_deliveries
        ORDER BY id DESC
        LIMIT 12
        """
    ).fetchall()
    lines = [
        "# Automation Status",
        "",
        f"- Live API requests today: {int(usage['requests'] or 0)}",
        f"- Matches pending final result: {int(pending['matches'] or 0)}",
        f"- Latest data activity: {freshness['freshness'] or 'n/a'}",
        f"- Notifications pending: {int(notification_counts['pending'] or 0)}",
        f"- Notifications sent: {int(notification_counts['sent'] or 0)}",
        f"- Notifications failed: {int(notification_counts['failed'] or 0)}",
        "",
        "## Recent runs",
    ]
    lines.extend(
        f"- `{row['run_key']}`: {row['status']} ({row['action']})"
        for row in runs
    )
    if not runs:
        lines.append("- No runs recorded.")
    lines.extend(["", "## Recent notifications"])
    lines.extend(
        (
            f"- `{row['match_id']}:{row['window_label']}:{row['channel']}`: "
            f"{row['status']}"
            + (f" ({row['error_code']})" if row["error_code"] else "")
        )
        for row in recent_notifications
    )
    if not recent_notifications:
        lines.append("- No notifications recorded.")
    _atomic_write_text(path, "\n".join(lines))


def write_automation_status(
    connection: sqlite3.Connection,
    path: Path | None = None,
) -> Path:
    output_path = path or (get_settings().logs_dir / "automation_status.md")
    _write_automation_status(connection, output_path)
    return output_path


def rebuild_outputs(
    connection: sqlite3.Connection | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    connection = connection or get_connection(settings.db_path)
    local_tz = ZoneInfo(settings.local_timezone)
    local_now = now.astimezone(local_tz) if now else datetime.now(local_tz)
    evaluation = evaluate_predictions(connection)
    from quiniela.player_evidence import evaluate_player_predictions

    player_evaluation = evaluate_player_predictions(connection)
    history = _history_frame(connection)
    latest = _latest_frame(history, local_now)
    timelines = _timeline_by_match(history)

    _atomic_write_dataframe(
        latest,
        settings.predictions_dir / "predictions_latest.csv",
    )
    _atomic_write_text(
        settings.predictions_dir / "predictions_latest.json",
        _json_records(latest),
    )
    _atomic_write_dataframe(
        history,
        settings.predictions_dir / "predictions_history.csv",
    )
    _atomic_write_text(
        settings.predictions_dir / "predictions_history.json",
        _json_records(history),
    )

    all_rows = _html_rows(connection, latest, timelines)
    today_rows = [
        row for row in all_rows if str(row["date_cdmx"]) == local_now.date().isoformat()
    ]
    fixture_ids = sorted(
        {
            str(int(row["api_fixture_id"]))
            for row in all_rows
            if row.get("api_fixture_id") not in (None, "")
            and str(row.get("api_fixture_id")) != "nan"
        }
    )
    lineups = _load_lineups_by_fixture(connection, fixture_ids)
    prediction_ids = [
        int(row["prediction_id"])
        for row in all_rows
        if row.get("prediction_id") is not None
    ]
    impacts = _load_prediction_impacts(
        connection,
        [str(row["match_id"]) for row in all_rows],
        prediction_ids=prediction_ids,
    )
    _fill_snapshot_impacts(connection, all_rows, impacts)
    _atomic_write_text(
        settings.predictions_dir / "index.html",
        render_predictions_html(all_rows, lineups, impacts),
    )
    _atomic_write_text(
        settings.predictions_dir / "today.html",
        render_predictions_html(today_rows, lineups, impacts),
    )
    _write_data_quality(connection, settings.logs_dir / "data_quality.md")
    _write_model_performance(connection, settings.logs_dir / "model_performance.md")
    write_automation_status(
        connection,
        settings.logs_dir / "automation_status.md",
    )
    return {
        "history_rows": len(history),
        "latest_rows": len(latest),
        "today_rows": len(today_rows),
        "evaluation": evaluation,
        "player_evaluation": player_evaluation,
        "files": sorted(STABLE_PREDICTION_FILES | STABLE_LOG_FILES),
    }


def obsolete_output_files() -> list[Path]:
    settings = get_settings()
    files = []
    for path in settings.predictions_dir.iterdir():
        if not path.is_file() or path.name in STABLE_PREDICTION_FILES:
            continue
        if path.suffix.lower() in {".html", ".csv", ".json"}:
            files.append(path)
    for path in settings.logs_dir.iterdir():
        if not path.is_file() or path.name in STABLE_LOG_FILES:
            continue
        if path.suffix.lower() in {".md", ".csv", ".json"}:
            files.append(path)
    return sorted(files)


def cleanup_obsolete_outputs(apply: bool = False) -> dict[str, Any]:
    files = obsolete_output_files()
    if apply:
        for path in files:
            path.unlink()
    return {
        "apply": apply,
        "count": len(files),
        "files": [str(path) for path in files],
    }
