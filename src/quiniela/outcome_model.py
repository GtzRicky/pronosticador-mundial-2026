from __future__ import annotations

from datetime import datetime, timezone
import pickle
from pathlib import Path
import sqlite3
from typing import Any

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, log_loss
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from quiniela.config import get_settings
from quiniela.db import fetch_dataframe


MODEL_ARTIFACT_NAME = "logit_outcome_v1.pkl"
MODEL_VERSION = "logit_outcome_v1"
OUTCOME_CLASSES = ("away", "draw", "home")
OUTCOME_MODEL_FEATURE_COLUMNS = [
    "delta_recent_form",
    "delta_goal_difference",
    "delta_odds",
    "delta_attack_strength",
    "delta_defense_strength",
    "delta_midfield_control",
    "delta_goalkeeper_strength",
    "delta_bench_impact",
    "delta_discipline_risk_penalty",
    "delta_host_adjustment",
]


def _safe_float(value: Any) -> float:
    try:
        if value is None or pd.isna(value):
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def outcome_feature_values(row: dict[str, Any]) -> dict[str, float]:
    return {
        "delta_recent_form": _safe_float(row.get("home_recent_form_score"))
        - _safe_float(row.get("away_recent_form_score")),
        "delta_goal_difference": _safe_float(row.get("home_gd_avg"))
        - _safe_float(row.get("away_gd_avg")),
        "delta_odds": _safe_float(row.get("home_odds_adjustment"))
        - _safe_float(row.get("away_odds_adjustment")),
        "delta_attack_strength": _safe_float(row.get("delta_attack_strength")),
        "delta_defense_strength": _safe_float(row.get("delta_defense_strength")),
        "delta_midfield_control": _safe_float(row.get("delta_midfield_control")),
        "delta_goalkeeper_strength": _safe_float(row.get("delta_goalkeeper_strength")),
        "delta_bench_impact": _safe_float(row.get("delta_bench_impact")),
        "delta_discipline_risk_penalty": _safe_float(
            row.get("delta_discipline_risk_penalty")
        ),
        "delta_host_adjustment": _safe_float(row.get("home_host_adjustment"))
        - _safe_float(row.get("away_host_adjustment")),
    }


def _outcome_label(home_goals: Any, away_goals: Any) -> str:
    home = _safe_float(home_goals)
    away = _safe_float(away_goals)
    if home > away:
        return "home"
    if home < away:
        return "away"
    return "draw"


def build_outcome_training_dataset(
    connection: sqlite3.Connection,
    window: int = 5,
) -> pd.DataFrame:
    from quiniela.features import build_match_feature_row

    historical_df = fetch_dataframe(
        connection,
        """
        SELECT fixture_id, match_date, home_team, away_team,
               home_team_norm, away_team_norm, home_goals, away_goals
        FROM historical_matches
        WHERE home_goals IS NOT NULL
          AND away_goals IS NOT NULL
          AND match_date IS NOT NULL
        ORDER BY match_date, fixture_id
        """,
    )
    rows: list[dict[str, Any]] = []
    for match in historical_df.to_dict(orient="records"):
        feature_row = build_match_feature_row(
            connection,
            {
                "match_id": f"historical_{match['fixture_id']}",
                "date_cdmx": str(match["match_date"])[:10],
                "datetime_cdmx": match["match_date"],
                "group": "historical",
                "home_team": match["home_team"],
                "away_team": match["away_team"],
                "home_team_norm": match["home_team_norm"],
                "away_team_norm": match["away_team_norm"],
                # Confirmed lineups are pre-match information; match statistics
                # remain excluded by the strict match_date filters in features.
                "api_fixture_id": match["fixture_id"],
                "stage": "historical",
            },
            window=window,
            allow_season_stats=False,
        )
        rows.append(
            {
                "fixture_id": str(match["fixture_id"]),
                "match_date": match["match_date"],
                **outcome_feature_values(feature_row),
                "outcome": _outcome_label(match["home_goals"], match["away_goals"]),
            }
        )
    return pd.DataFrame(rows)


def _new_pipeline(c_value: float) -> Pipeline:
    return Pipeline(
        [
            ("scaler", StandardScaler()),
            (
                "logit",
                LogisticRegression(
                    C=c_value,
                    penalty="l2",
                    solver="lbfgs",
                    class_weight="balanced",
                    max_iter=1000,
                    random_state=2026,
                ),
            ),
        ]
    )


def _expanding_splits(row_count: int) -> list[tuple[np.ndarray, np.ndarray]]:
    minimum_train = max(24, row_count // 2)
    remaining = row_count - minimum_train
    if remaining < 6:
        return []
    test_size = max(3, remaining // 3)
    splits: list[tuple[np.ndarray, np.ndarray]] = []
    train_end = minimum_train
    while train_end < row_count:
        test_end = min(train_end + test_size, row_count)
        splits.append((np.arange(train_end), np.arange(train_end, test_end)))
        train_end = test_end
    return splits


def _aligned_probabilities(model: Pipeline, frame: pd.DataFrame) -> np.ndarray:
    probabilities = model.predict_proba(frame)
    classes = list(model.named_steps["logit"].classes_)
    aligned = np.zeros((len(frame), len(OUTCOME_CLASSES)), dtype=float)
    for target_index, label in enumerate(OUTCOME_CLASSES):
        if label in classes:
            aligned[:, target_index] = probabilities[:, classes.index(label)]
    row_sums = aligned.sum(axis=1, keepdims=True)
    row_sums[row_sums == 0.0] = 1.0
    return aligned / row_sums


def _brier_multiclass(labels: pd.Series, probabilities: np.ndarray) -> float:
    encoded = np.zeros_like(probabilities)
    for index, label in enumerate(labels):
        encoded[index, OUTCOME_CLASSES.index(str(label))] = 1.0
    return float(np.mean(np.sum((probabilities - encoded) ** 2, axis=1)))


def _calibration_error(labels: pd.Series, probabilities: np.ndarray) -> float:
    errors: list[float] = []
    bins = np.linspace(0.0, 1.0, 6)
    for class_index, label in enumerate(OUTCOME_CLASSES):
        observed = (labels.to_numpy() == label).astype(float)
        predicted = probabilities[:, class_index]
        for lower, upper in zip(bins[:-1], bins[1:]):
            mask = (predicted >= lower) & (
                predicted <= upper if upper == 1.0 else predicted < upper
            )
            if mask.any():
                errors.append(float(abs(predicted[mask].mean() - observed[mask].mean())))
    return float(np.mean(errors)) if errors else 0.0


def _validation_metrics(dataset: pd.DataFrame, c_value: float) -> dict[str, float] | None:
    probability_rows: list[np.ndarray] = []
    label_rows: list[str] = []
    for train_indices, test_indices in _expanding_splits(len(dataset)):
        train = dataset.iloc[train_indices]
        test = dataset.iloc[test_indices]
        if set(train["outcome"]) != set(OUTCOME_CLASSES):
            continue
        model = _new_pipeline(c_value)
        model.fit(train[OUTCOME_MODEL_FEATURE_COLUMNS], train["outcome"])
        probability_rows.append(
            _aligned_probabilities(model, test[OUTCOME_MODEL_FEATURE_COLUMNS])
        )
        label_rows.extend(test["outcome"].astype(str).tolist())
    if not probability_rows:
        return None
    probabilities = np.vstack(probability_rows)
    labels = pd.Series(label_rows)
    predicted = [OUTCOME_CLASSES[index] for index in probabilities.argmax(axis=1)]
    return {
        "log_loss": float(log_loss(labels, probabilities, labels=list(OUTCOME_CLASSES))),
        "brier_score": _brier_multiclass(labels, probabilities),
        "accuracy": float(accuracy_score(labels, predicted)),
        "calibration_error": _calibration_error(labels, probabilities),
        "validation_matches": int(len(labels)),
    }


def _frequency_baseline_metrics(dataset: pd.DataFrame) -> dict[str, float] | None:
    probability_rows: list[np.ndarray] = []
    label_rows: list[str] = []
    for train_indices, test_indices in _expanding_splits(len(dataset)):
        train = dataset.iloc[train_indices]
        test = dataset.iloc[test_indices]
        counts = train["outcome"].value_counts()
        total = float(counts.sum())
        if total <= 0.0:
            continue
        probability = np.array(
            [float(counts.get(label, 0)) / total for label in OUTCOME_CLASSES]
        )
        probability_rows.append(np.tile(probability, (len(test), 1)))
        label_rows.extend(test["outcome"].astype(str).tolist())
    if not probability_rows:
        return None
    probabilities = np.vstack(probability_rows)
    labels = pd.Series(label_rows)
    predicted = [OUTCOME_CLASSES[index] for index in probabilities.argmax(axis=1)]
    return {
        "log_loss": float(log_loss(labels, probabilities, labels=list(OUTCOME_CLASSES))),
        "brier_score": _brier_multiclass(labels, probabilities),
        "accuracy": float(accuracy_score(labels, predicted)),
        "calibration_error": _calibration_error(labels, probabilities),
        "validation_matches": int(len(labels)),
    }


def _interpretability_payload(model: Pipeline) -> dict[str, Any]:
    logit = model.named_steps["logit"]
    coefficients: dict[str, dict[str, float]] = {}
    odds_ratios: dict[str, dict[str, float]] = {}
    for class_index, label in enumerate(logit.classes_):
        coefficients[str(label)] = {
            feature: float(value)
            for feature, value in zip(
                OUTCOME_MODEL_FEATURE_COLUMNS,
                logit.coef_[class_index],
            )
        }
        odds_ratios[str(label)] = {
            feature: float(np.exp(value))
            for feature, value in zip(
                OUTCOME_MODEL_FEATURE_COLUMNS,
                logit.coef_[class_index],
            )
        }

    baseline = pd.DataFrame(
        [{feature: 0.0 for feature in OUTCOME_MODEL_FEATURE_COLUMNS}]
    )
    baseline_probabilities = _aligned_probabilities(model, baseline)[0]
    marginal_changes: dict[str, dict[str, float]] = {}
    for feature in OUTCOME_MODEL_FEATURE_COLUMNS:
        scenario = baseline.copy()
        scenario.loc[0, feature] = 1.0
        scenario_probabilities = _aligned_probabilities(model, scenario)[0]
        marginal_changes[feature] = {
            label: float(scenario_probabilities[index] - baseline_probabilities[index])
            for index, label in enumerate(OUTCOME_CLASSES)
        }
    return {
        "coefficients_standardized": coefficients,
        "odds_ratios_per_standard_deviation": odds_ratios,
        "probability_change_from_zero_to_one_raw_unit": marginal_changes,
    }


def write_outcome_model_report(bundle: dict[str, Any], output_path: Path) -> Path:
    metrics = bundle.get("metrics", {})
    baseline_metrics = bundle.get("frequency_baseline_metrics", {})
    interpretation = bundle.get("interpretability", {})
    lines = [
        "# Outcome Logit Model Report",
        "",
        f"- Model: {bundle.get('model_version')}",
        f"- Trained at: {bundle.get('trained_at')}",
        f"- Training matches: {bundle.get('training_matches')}",
        f"- Class counts: {bundle.get('class_counts')}",
        f"- Selected C: {bundle.get('selected_c')}",
        f"- Preliminary: {bundle.get('preliminary', True)}",
        "",
        "## Temporal validation",
        "",
        f"- Log loss: {metrics.get('log_loss', 0.0):.4f}",
        f"- Multiclass Brier score: {metrics.get('brier_score', 0.0):.4f}",
        f"- Accuracy: {metrics.get('accuracy', 0.0):.4f}",
        f"- Calibration error: {metrics.get('calibration_error', 0.0):.4f}",
        "",
        "## Expanding frequency baseline",
        "",
        f"- Log loss: {baseline_metrics.get('log_loss', 0.0):.4f}",
        f"- Multiclass Brier score: {baseline_metrics.get('brier_score', 0.0):.4f}",
        f"- Accuracy: {baseline_metrics.get('accuracy', 0.0):.4f}",
        f"- Calibration error: {baseline_metrics.get('calibration_error', 0.0):.4f}",
        "",
        "## Interpretation",
        "",
        "Coefficients and odds ratios use standardized inputs. They are descriptive, not robust significance tests.",
        "",
    ]
    odds = interpretation.get("odds_ratios_per_standard_deviation", {})
    for label in OUTCOME_CLASSES:
        lines.append(f"### {label}")
        for feature, value in odds.get(label, {}).items():
            lines.append(f"- {feature}: {value:.3f}")
        lines.append("")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")
    return output_path


def train_outcome_model(
    connection: sqlite3.Connection,
    artifact_path: Path | None = None,
    min_matches: int = 40,
    min_per_class: int = 8,
) -> dict[str, Any]:
    settings = get_settings()
    artifact_path = artifact_path or settings.model_artifacts_dir / MODEL_ARTIFACT_NAME
    dataset = build_outcome_training_dataset(connection)
    if len(dataset) < min_matches:
        return {
            "trained": False,
            "reason": "insufficient_matches",
            "matches": int(len(dataset)),
            "required_min_matches": min_matches,
        }
    class_counts = dataset["outcome"].value_counts().to_dict()
    missing_or_small = {
        label: int(class_counts.get(label, 0))
        for label in OUTCOME_CLASSES
        if int(class_counts.get(label, 0)) < min_per_class
    }
    if missing_or_small:
        return {
            "trained": False,
            "reason": "insufficient_class_coverage",
            "matches": int(len(dataset)),
            "class_counts": class_counts,
            "required_min_per_class": min_per_class,
        }

    candidates = (0.05, 0.1, 0.25, 0.5, 1.0)
    evaluated = [
        (c_value, _validation_metrics(dataset, c_value)) for c_value in candidates
    ]
    evaluated = [(c_value, metrics) for c_value, metrics in evaluated if metrics]
    if not evaluated:
        return {
            "trained": False,
            "reason": "insufficient_temporal_folds",
            "matches": int(len(dataset)),
            "class_counts": class_counts,
        }
    selected_c, metrics = min(evaluated, key=lambda item: item[1]["log_loss"])
    baseline_metrics = _frequency_baseline_metrics(dataset) or {}
    model = _new_pipeline(selected_c)
    model.fit(dataset[OUTCOME_MODEL_FEATURE_COLUMNS], dataset["outcome"])
    bundle = {
        "model_version": MODEL_VERSION,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "feature_columns": OUTCOME_MODEL_FEATURE_COLUMNS,
        "classes": list(OUTCOME_CLASSES),
        "pipeline": model,
        "training_matches": int(len(dataset)),
        "class_counts": {key: int(value) for key, value in class_counts.items()},
        "selected_c": selected_c,
        "metrics": metrics,
        "frequency_baseline_metrics": baseline_metrics,
        "candidate_metrics": {
            str(c_value): candidate_metrics
            for c_value, candidate_metrics in evaluated
        },
        "preliminary": len(dataset) < 120,
        "interpretability": _interpretability_payload(model),
    }
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    with artifact_path.open("wb") as handle:
        pickle.dump(bundle, handle)
    report_path = write_outcome_model_report(
        bundle,
        settings.logs_dir / "model_performance.md",
    )
    return {
        "trained": True,
        "artifact_path": str(artifact_path),
        "report_path": str(report_path),
        "model_version": MODEL_VERSION,
        "training_matches": int(len(dataset)),
        "class_counts": bundle["class_counts"],
        "selected_c": selected_c,
        "metrics": metrics,
        "frequency_baseline_metrics": baseline_metrics,
        "preliminary": bundle["preliminary"],
    }


def load_outcome_model_artifact(
    artifact_path: Path | None = None,
) -> dict[str, Any] | None:
    if artifact_path is None:
        from quiniela.player_evidence import load_active_release_bundle

        active_bundle = load_active_release_bundle()
        if active_bundle is not None and active_bundle.get("outcome_pipeline") is not None:
            return {
                "model_version": active_bundle.get("model_version"),
                "pipeline": active_bundle["outcome_pipeline"],
                "feature_columns": active_bundle.get(
                    "outcome_feature_columns",
                    OUTCOME_MODEL_FEATURE_COLUMNS,
                ),
                "preliminary": True,
            }
    settings = get_settings()
    artifact_path = artifact_path or settings.model_artifacts_dir / MODEL_ARTIFACT_NAME
    if not artifact_path.exists():
        return None
    with artifact_path.open("rb") as handle:
        return pickle.load(handle)


def predict_outcome_probabilities(
    bundle: dict[str, Any] | None,
    feature_row: dict[str, Any],
) -> dict[str, float] | None:
    if not bundle or bundle.get("pipeline") is None:
        return None
    feature_columns = bundle.get("feature_columns", OUTCOME_MODEL_FEATURE_COLUMNS)
    if list(feature_columns) == list(OUTCOME_MODEL_FEATURE_COLUMNS):
        values = outcome_feature_values(feature_row)
    else:
        values = {
            column: _safe_float(feature_row.get(column))
            for column in feature_columns
        }
    frame = pd.DataFrame([values], columns=feature_columns)
    probabilities = _aligned_probabilities(bundle["pipeline"], frame)[0]
    return {
        label: float(probabilities[index])
        for index, label in enumerate(OUTCOME_CLASSES)
    }


def poisson_outcome_probabilities(matrix: np.ndarray) -> dict[str, float]:
    home = float(np.tril(matrix, k=-1).sum())
    draw = float(np.trace(matrix))
    away = float(np.triu(matrix, k=1).sum())
    total = home + draw + away
    if total <= 0.0:
        return {"home": 1 / 3, "draw": 1 / 3, "away": 1 / 3}
    return {"home": home / total, "draw": draw / total, "away": away / total}


def hybridize_score_matrix(
    poisson_matrix: np.ndarray,
    outcome_probabilities: dict[str, float] | None,
) -> np.ndarray:
    matrix = np.asarray(poisson_matrix, dtype=float).copy()
    if not outcome_probabilities:
        return matrix / matrix.sum()
    poisson_totals = poisson_outcome_probabilities(matrix)
    masks = {
        "home": np.fromfunction(lambda i, j: i > j, matrix.shape, dtype=int),
        "draw": np.fromfunction(lambda i, j: i == j, matrix.shape, dtype=int),
        "away": np.fromfunction(lambda i, j: i < j, matrix.shape, dtype=int),
    }
    for label, mask in masks.items():
        target = max(float(outcome_probabilities.get(label, 0.0)), 0.0)
        baseline = poisson_totals[label]
        if baseline > 0.0:
            matrix[mask] *= target / baseline
    total = matrix.sum()
    return matrix / total if total > 0.0 else np.asarray(poisson_matrix, dtype=float)
