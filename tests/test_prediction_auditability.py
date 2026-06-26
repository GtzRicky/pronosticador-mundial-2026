from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pandas as pd

from quiniela.db import get_connection, insert_prediction_rows
import quiniela.output_manager as outputs


TZ = ZoneInfo("America/Mexico_City")


def _settings(tmp_path: Path) -> SimpleNamespace:
    predictions = tmp_path / "predictions"
    logs = tmp_path / "logs"
    predictions.mkdir()
    logs.mkdir()
    return SimpleNamespace(
        db_path=tmp_path / "auditability.sqlite",
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
            'match-audit', '2026-06-13', '15:00', '2026-06-13T15:00:00-04:00',
            '2026-06-13', '13:00', '2026-06-13T13:00:00-06:00',
            'Catar', 'Suiza', 'qatar', 'switzerland',
            'B', 'Estadio', 'group', 'NS', 100
        )
        """
    )
    connection.commit()


def _prediction(*, generated_at: str, pre_kickoff: int) -> dict:
    return {
        "match_id": "match-audit",
        "datetime_cdmx": "2026-06-13T13:00:00-06:00",
        "group": "B",
        "home_team": "Catar",
        "away_team": "Suiza",
        "predicted_score": "0-1",
        "probability": 0.2,
        "model_version": "poisson-test",
        "hybrid_predicted_score": "0-1",
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
    }


def test_prediction_auditability_columns_are_migrated(tmp_path: Path) -> None:
    connection = get_connection(tmp_path / "schema.sqlite")

    columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(predictions)").fetchall()
    }

    assert {
        "audit_snapshot_id",
        "audit_lineup_sources_json",
        "audit_odds_source_json",
        "audit_degradation_reasons_json",
        "not_evaluable_reason",
    }.issubset(columns)


def test_post_kickoff_predictions_are_not_evaluable_with_valid_degradation_json(
    tmp_path: Path,
) -> None:
    connection = get_connection(tmp_path / "post-kickoff.sqlite")
    _seed_match(connection)

    insert_prediction_rows(
        connection,
        pd.DataFrame(
            [
                _prediction(
                    generated_at="2026-06-13T19:05:00+00:00",
                    pre_kickoff=0,
                )
            ]
        ),
    )

    row = connection.execute(
        """
        SELECT not_evaluable_reason,
               audit_degradation_reasons_json,
               json_valid(audit_degradation_reasons_json) AS valid
        FROM predictions
        """
    ).fetchone()

    assert row["not_evaluable_reason"] == "post_kickoff_prediction"
    assert row["valid"] == 1
    assert json.loads(row["audit_degradation_reasons_json"]) == [
        "post_kickoff_prediction"
    ]


def test_auditability_fields_are_preserved_in_stable_exports(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = _settings(tmp_path)
    monkeypatch.setattr(outputs, "get_settings", lambda: settings)
    connection = get_connection(settings.db_path)
    _seed_match(connection)
    insert_prediction_rows(
        connection,
        pd.DataFrame(
            [
                {
                    **_prediction(
                        generated_at="2026-06-13T18:30:00+00:00",
                        pre_kickoff=1,
                    ),
                    "audit_snapshot_id": 7,
                    "audit_lineup_sources_json": {"home": "official", "away": "web"},
                    "audit_odds_source_json": {"consensus_available": True},
                    "audit_degradation_reasons_json": [],
                }
            ]
        ),
    )

    summary = outputs.rebuild_outputs(
        connection,
        now=datetime(2026, 6, 13, 12, 0, tzinfo=TZ),
    )

    latest = pd.read_csv(settings.predictions_dir / "predictions_latest.csv")
    assert summary["history_rows"] == 1
    assert int(latest.iloc[0]["audit_snapshot_id"]) == 7
    assert json.loads(latest.iloc[0]["audit_lineup_sources_json"]) == {
        "home": "official",
        "away": "web",
    }
    assert json.loads(latest.iloc[0]["audit_degradation_reasons_json"]) == []
    assert (settings.predictions_dir / "index.html").exists()
