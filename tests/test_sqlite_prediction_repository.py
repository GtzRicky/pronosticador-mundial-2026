from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from quiniela.adapters.outbound.sqlite.prediction_repository import SQLitePredictionRepository
from quiniela.db import get_connection, insert_prediction_rows


def _predictions_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "match_id": "match-1",
                "datetime_cdmx": "2026-06-11T16:00:00-06:00",
                "group": "A",
                "home_team": "Mexico",
                "away_team": "Canada",
                "predicted_score": "1-0",
                "probability": 0.23,
                "model_version": "poisson_v1",
                "hybrid_predicted_score": "1-0",
                "hybrid_probability": 0.25,
                "home_win_probability": 0.5,
                "draw_probability": 0.3,
                "away_win_probability": 0.2,
                "outcome_model_version": "poisson_fallback",
                "data_freshness_at": "2026-06-11T15:00:00Z",
                "prediction_context": "manual",
                "window_label": None,
                "generated_at": "2026-06-11T15:00:00Z",
                "generated_at_utc": "2026-06-11T15:00:00Z",
                "is_pre_kickoff": 1,
                "odds_consensus": {"home": np.nan},
            }
        ]
    )


def test_sqlite_prediction_repository_inserts_history_rows(tmp_path: Path) -> None:
    connection = get_connection(tmp_path / "predictions.sqlite")
    repository = SQLitePredictionRepository(connection)

    first_ids = repository.insert_prediction_rows(_predictions_frame())
    second_ids = repository.insert_prediction_rows(_predictions_frame())

    rows = connection.execute("SELECT id, source_json FROM predictions ORDER BY id").fetchall()
    assert len(rows) == 2
    assert first_ids != second_ids
    assert json.loads(rows[0]["source_json"])["odds_consensus"] == {"home": None}


def test_insert_prediction_rows_wrapper_delegates_to_repository(tmp_path: Path) -> None:
    connection = get_connection(tmp_path / "predictions-wrapper.sqlite")

    inserted_ids = insert_prediction_rows(connection, _predictions_frame())

    row = connection.execute("SELECT id, predicted_score FROM predictions").fetchone()
    assert inserted_ids == [row["id"]]
    assert row["predicted_score"] == "1-0"
