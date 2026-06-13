from pathlib import Path

import numpy as np
import pandas as pd
import quiniela.outcome_model as outcome_model
from quiniela.db import get_connection
from quiniela.outcome_model import (
    OUTCOME_CLASSES,
    OUTCOME_MODEL_FEATURE_COLUMNS,
    hybridize_score_matrix,
    poisson_outcome_probabilities,
    predict_outcome_probabilities,
    train_outcome_model,
)


def _dataset(rows_per_class: int = 14) -> pd.DataFrame:
    rows = []
    index = 0
    for class_index, label in enumerate(OUTCOME_CLASSES):
        for sample in range(rows_per_class):
            values = {
                feature: float(class_index * 2 + sample / rows_per_class)
                for feature in OUTCOME_MODEL_FEATURE_COLUMNS
            }
            rows.append(
                {
                    "fixture_id": str(index),
                    "match_date": f"2025-01-{(index % 28) + 1:02d}T00:00:00+00:00",
                    **values,
                    "outcome": label,
                }
            )
            index += 1
    return pd.DataFrame(rows).sort_values(["match_date", "fixture_id"]).reset_index(drop=True)


def test_predict_outcome_probabilities_sum_to_one() -> None:
    frame = _dataset(4)
    pipeline = outcome_model._new_pipeline(0.1)
    pipeline.fit(frame[OUTCOME_MODEL_FEATURE_COLUMNS], frame["outcome"])
    bundle = {"pipeline": pipeline}
    probabilities = predict_outcome_probabilities(bundle, {})

    assert probabilities is not None
    assert abs(sum(probabilities.values()) - 1.0) < 1e-9
    assert all(0.0 <= value <= 1.0 for value in probabilities.values())


def test_hybrid_matrix_reproduces_target_outcome_probabilities() -> None:
    matrix = np.array(
        [
            [0.20, 0.08, 0.02],
            [0.12, 0.20, 0.05],
            [0.04, 0.09, 0.20],
        ]
    )
    matrix /= matrix.sum()
    target = {"home": 0.55, "draw": 0.25, "away": 0.20}

    hybrid = hybridize_score_matrix(matrix, target)
    totals = poisson_outcome_probabilities(hybrid)

    assert abs(hybrid.sum() - 1.0) < 1e-9
    assert all(abs(totals[key] - target[key]) < 1e-9 for key in target)


def test_train_outcome_model_rejects_small_and_incomplete_samples(
    tmp_path: Path,
    monkeypatch,
) -> None:
    connection = get_connection(tmp_path / "model.sqlite")
    monkeypatch.setattr(outcome_model, "build_outcome_training_dataset", lambda _: _dataset(5))
    small = train_outcome_model(connection, tmp_path / "small.pkl")
    assert small["reason"] == "insufficient_matches"

    incomplete = _dataset(14)
    incomplete = incomplete[incomplete["outcome"] != "draw"]
    monkeypatch.setattr(outcome_model, "build_outcome_training_dataset", lambda _: incomplete)
    missing_class = train_outcome_model(
        connection,
        tmp_path / "incomplete.pkl",
        min_matches=20,
    )
    assert missing_class["reason"] == "insufficient_class_coverage"


def test_train_outcome_model_builds_artifact(tmp_path: Path, monkeypatch) -> None:
    connection = get_connection(tmp_path / "model.sqlite")
    monkeypatch.setattr(outcome_model, "build_outcome_training_dataset", lambda _: _dataset())
    monkeypatch.setattr(
        outcome_model,
        "_validation_metrics",
        lambda dataset, c_value: {
            "log_loss": float(c_value),
            "brier_score": 0.4,
            "accuracy": 0.5,
            "calibration_error": 0.1,
            "validation_matches": 12,
        },
    )
    monkeypatch.setattr(
        outcome_model,
        "write_outcome_model_report",
        lambda bundle, output_path: tmp_path / "report.md",
    )
    artifact = tmp_path / "logit.pkl"

    summary = train_outcome_model(connection, artifact)

    assert summary["trained"] is True
    assert summary["selected_c"] == 0.05
    assert artifact.exists()
