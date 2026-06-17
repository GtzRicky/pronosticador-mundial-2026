from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pandas as pd

from quiniela.db import (
    claim_automation_run,
    finish_automation_run,
    get_connection,
    insert_prediction_player_impacts,
    insert_prediction_rows,
)
import quiniela.output_manager as outputs


TZ = ZoneInfo("America/Mexico_City")


def _settings(tmp_path: Path) -> SimpleNamespace:
    predictions = tmp_path / "predictions"
    logs = tmp_path / "logs"
    predictions.mkdir()
    logs.mkdir()
    return SimpleNamespace(
        db_path=tmp_path / "outputs.sqlite",
        predictions_dir=predictions,
        logs_dir=logs,
        local_timezone="America/Mexico_City",
    )


def _seed_match(connection) -> None:
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
            'Catar', 'Suiza', 'qatar', 'switzerland',
            'B', 'Estadio', 'group', 'FT', 100
        )
        """
    )
    connection.execute(
        """
        INSERT INTO actual_results (match_id, home_goals, away_goals, result_json)
        VALUES ('match-1', 0, 2, '{}')
        """
    )
    connection.commit()


def _prediction(
    generated_at: str,
    score: str,
    *,
    pre_kickoff: int,
) -> dict:
    return {
        "match_id": "match-1",
        "datetime_cdmx": "2026-06-13T13:00:00-06:00",
        "group": "B",
        "home_team": "Catar",
        "away_team": "Suiza",
        "predicted_score": score,
        "probability": 0.2,
        "model_version": "poisson-test",
        "hybrid_predicted_score": score,
        "hybrid_probability": 0.2,
        "home_win_probability": 0.1,
        "draw_probability": 0.2,
        "away_win_probability": 0.7,
        "outcome_model_version": "logit-test",
        "data_freshness_at": generated_at,
        "prediction_context": "pre_match" if pre_kickoff else "hourly",
        "window_label": "t-1" if pre_kickoff else None,
        "generated_at": generated_at,
        "generated_at_utc": generated_at,
        "is_pre_kickoff": pre_kickoff,
        "lambda_home": 0.7,
        "lambda_away": 1.8,
    }


def test_canonical_prediction_uses_real_timestamp_and_exports_all_versions(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = _settings(tmp_path)
    monkeypatch.setattr(outputs, "get_settings", lambda: settings)
    connection = get_connection(settings.db_path)
    _seed_match(connection)
    prediction_ids = insert_prediction_rows(
        connection,
        pd.DataFrame(
            [
                _prediction("2026-06-13T18:59:00+00:00", "0-1", pre_kickoff=1),
                _prediction("2026-06-13T19:05:00+00:00", "1-1", pre_kickoff=0),
            ]
        ),
    )
    insert_prediction_player_impacts(
        connection,
        prediction_ids[0],
        "match-1",
        [
            {
                "team_norm": "switzerland",
                "player_name": "Player One",
                "role_bucket": "forward",
                "net_impact": 0.5,
            }
        ],
    )

    summary = outputs.rebuild_outputs(
        connection,
        now=datetime(2026, 6, 13, 14, 0, tzinfo=TZ),
    )

    assert summary["history_rows"] == 2
    assert summary["latest_rows"] == 1
    assert summary["generated_at_cdmx"] == "2026-06-13 14:00:00 CDMX"
    latest = pd.read_csv(settings.predictions_dir / "predictions_latest.csv")
    history = pd.read_csv(settings.predictions_dir / "predictions_history.csv")
    assert latest.iloc[0]["predicted_score"] == "0-1"
    assert int(latest.iloc[0]["is_canonical"]) == 1
    assert len(history) == 2
    assert history["is_canonical"].sum() == 1
    assert (settings.predictions_dir / "index.html").exists()
    assert (settings.predictions_dir / "today.html").exists()
    html = (settings.predictions_dir / "index.html").read_text(encoding="utf-8")
    assert "Historial prepartido (1)" in html
    assert "MAE goles" in html
    assert "Ultima actualizacion: 2026-06-13 14:00:00 CDMX" in html
    assert not list(settings.predictions_dir.glob(".*.tmp"))


def test_impacts_are_append_only_per_prediction(tmp_path: Path) -> None:
    connection = get_connection(tmp_path / "impacts.sqlite")
    _seed_match(connection)
    ids = insert_prediction_rows(
        connection,
        pd.DataFrame(
            [
                _prediction("2026-06-13T18:30:00+00:00", "0-1", pre_kickoff=1),
                _prediction("2026-06-13T18:59:00+00:00", "0-2", pre_kickoff=1),
            ]
        ),
    )
    for prediction_id in ids:
        insert_prediction_player_impacts(
            connection,
            prediction_id,
            "match-1",
            [
                {
                    "team_norm": "switzerland",
                    "player_name": f"Player {prediction_id}",
                    "role_bucket": "forward",
                }
            ],
        )

    rows = connection.execute(
        """
        SELECT prediction_id, player_name
        FROM prediction_player_impacts
        ORDER BY prediction_id
        """
    ).fetchall()
    assert [(row["prediction_id"], row["player_name"]) for row in rows] == [
        (ids[0], f"Player {ids[0]}"),
        (ids[1], f"Player {ids[1]}"),
    ]


def test_cleanup_obsolete_outputs_has_dry_run_and_apply(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = _settings(tmp_path)
    monkeypatch.setattr(outputs, "get_settings", lambda: settings)
    obsolete = settings.predictions_dir / "predicciones_2026-06-13.html"
    stable = settings.predictions_dir / "index.html"
    obsolete.write_text("old", encoding="utf-8")
    stable.write_text("new", encoding="utf-8")

    dry_run = outputs.cleanup_obsolete_outputs(apply=False)
    assert dry_run["count"] == 1
    assert obsolete.exists()

    applied = outputs.cleanup_obsolete_outputs(apply=True)
    assert applied["count"] == 1
    assert not obsolete.exists()
    assert stable.exists()


def test_automation_status_marks_failed_run_with_degraded_outputs(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = _settings(tmp_path)
    monkeypatch.setattr(outputs, "get_settings", lambda: settings)
    connection = get_connection(settings.db_path)
    claim_automation_run(
        connection,
        run_key="hourly:2026-06-13T10",
        action="hourly",
        scheduled_for="2026-06-13T10:00:00-06:00",
        details={"action": "hourly"},
    )
    finish_automation_run(
        connection,
        "hourly:2026-06-13T10",
        "failed",
        {
            "error": "scheduled team mismatch",
            "degraded_outputs": {"source": "sqlite_latest", "latest_rows": 1},
            "retrieval_issues": [
                {
                    "reason": "scheduled_team_mismatch",
                    "fixture_id": "123",
                    "raw_home_team": "Mexico",
                    "raw_away_team": "Panama",
                }
            ],
        },
    )

    outputs.write_automation_status(connection)

    content = (settings.logs_dir / "automation_status.md").read_text(encoding="utf-8")
    assert "outputs rebuilt from sqlite_latest" in content
    assert "scheduled_team_mismatch" in content
