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
from quiniela.infrastructure.competition_config import resolve_competition_context


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
    assert summary["namespace"] == "world-cup-2026"
    assert summary["stable_aliases"] is True
    latest = pd.read_csv(settings.predictions_dir / "predictions_latest.csv")
    history = pd.read_csv(settings.predictions_dir / "predictions_history.csv")
    assert latest.iloc[0]["predicted_score"] == "0-1"
    assert latest.iloc[0]["competition_id"] == "fifa_world_cup"
    assert latest.iloc[0]["season_id"] == "world_cup_2026"
    assert "audit_degradation_reasons_json" in latest.columns
    assert "evidence_release_id" in latest.columns
    assert int(latest.iloc[0]["is_canonical"]) == 1
    assert len(history) == 2
    assert history["is_canonical"].sum() == 1
    assert (settings.predictions_dir / "index.html").exists()
    assert (settings.predictions_dir / "today.html").exists()
    namespaced = settings.predictions_dir / "world-cup-2026"
    assert (namespaced / "index.html").exists()
    assert (namespaced / "today.html").exists()
    assert (namespaced / "predictions_latest.json").exists()
    html = (settings.predictions_dir / "index.html").read_text(encoding="utf-8")
    assert "Historial prepartido (1)" in html
    assert "MAE goles" in html
    assert "Auditoria del pronostico" in html
    assert "Ultima actualizacion: 2026-06-13 14:00:00 CDMX" in html
    assert not list(settings.predictions_dir.glob(".*.tmp"))


def test_prediction_source_json_is_strict_json_when_nan_present(tmp_path: Path) -> None:
    connection = get_connection(tmp_path / "strict-json.sqlite")
    _seed_match(connection)

    insert_prediction_rows(
        connection,
        pd.DataFrame(
            [
                {
                    **_prediction("2026-06-13T18:59:00+00:00", "0-1", pre_kickoff=1),
                    "odds_adjusted_probability": float("nan"),
                    "odds_adjusted_exact_score": None,
                }
            ]
        ),
    )

    row = connection.execute(
        "SELECT json_valid(source_json) AS valid, source_json FROM predictions"
    ).fetchone()
    assert row["valid"] == 1
    assert "NaN" not in row["source_json"]


def test_non_default_outputs_are_namespaced_without_overwriting_world_cup_aliases(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = _settings(tmp_path)
    monkeypatch.setattr(outputs, "get_settings", lambda: settings)
    connection = get_connection(settings.db_path)
    context = resolve_competition_context("champions_league_2026_2027")
    root_alias = settings.predictions_dir / "predictions_latest.csv"
    root_alias.write_text("world-cup-sentinel\n", encoding="utf-8")
    with connection:
        connection.execute(
            """
            INSERT INTO matches (
                match_id, competition_id, season_id,
                date_et, time_et, datetime_et,
                date_cdmx, time_cdmx, datetime_cdmx,
                home_team, away_team, home_team_norm, away_team_norm,
                group_name, stadium, stage, status
            ) VALUES (
                'ucl-1', ?, ?,
                '2026-09-15', '16:00', '2026-09-15T16:00:00-04:00',
                '2026-09-15', '14:00',
                '2026-09-15T14:00:00-06:00',
                'Club A', 'Club B', 'club a', 'club b',
                'league', 'Arena', 'league', 'NS'
            )
            """,
            (context.competition_id, context.season_id),
        )
    row = {
        **_prediction("2026-09-15T18:00:00+00:00", "2-1", pre_kickoff=1),
        "match_id": "ucl-1",
        "competition_id": context.competition_id,
        "season_id": context.season_id,
        "datetime_cdmx": "2026-09-15T14:00:00-06:00",
        "home_team": "Club A",
        "away_team": "Club B",
    }
    insert_prediction_rows(connection, pd.DataFrame([row]))

    summary = outputs.rebuild_outputs(
        connection,
        now=datetime(2026, 9, 15, 13, 0, tzinfo=TZ),
        competition_context=context,
    )

    namespace = settings.predictions_dir / context.namespace
    latest = pd.read_csv(namespace / "predictions_latest.csv")
    assert summary["stable_aliases"] is False
    assert latest["match_id"].tolist() == ["ucl-1"]
    assert root_alias.read_text(encoding="utf-8") == "world-cup-sentinel\n"


def test_rebuild_outputs_tolerates_legacy_malformed_prediction_json(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = _settings(tmp_path)
    monkeypatch.setattr(outputs, "get_settings", lambda: settings)
    connection = get_connection(settings.db_path)
    _seed_match(connection)
    with connection:
        connection.execute(
            """
            INSERT INTO predictions (
                match_id, datetime_cdmx, group_name,
                home_team, away_team, predicted_score, probability,
                model_version, source_json, generated_at_utc,
                is_pre_kickoff
            ) VALUES (
                'match-1', '2026-06-13T13:00:00-06:00', 'B',
                'Catar', 'Suiza', '0-1', 0.2,
                'poisson-test', '{"odds_adjusted_probability": NaN}',
                '2026-06-13T18:59:00+00:00', 1
            )
            """
        )

    summary = outputs.rebuild_outputs(
        connection,
        now=datetime(2026, 6, 13, 14, 0, tzinfo=TZ),
    )

    assert summary["history_rows"] == 1
    assert (settings.predictions_dir / "today.html").exists()


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
