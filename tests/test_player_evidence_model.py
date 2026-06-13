from pathlib import Path

import pandas as pd

import quiniela.player_evidence as evidence
from quiniela.db import (
    activate_model_release as activate_db_release,
    get_active_model_release,
    get_connection,
    register_model_release,
)


def test_temporal_splits_keep_train_before_test() -> None:
    splits = evidence.temporal_match_splits(40)
    assert splits
    for train, test in splits:
        assert set(train).isdisjoint(set(test))
        assert train.max() < test.min()


def test_promotion_gate_requires_no_regressions_and_two_percent_gain() -> None:
    baseline = {
        "goal_mae": 1.0,
        "goal_poisson_deviance": 1.0,
        "log_loss": 1.0,
        "brier_score": 0.6,
        "calibration_error": 0.2,
    }
    candidate = {
        "goal_mae": 0.98,
        "goal_poisson_deviance": 0.97,
        "log_loss": 0.99,
        "brier_score": 0.59,
        "calibration_error": 0.19,
    }
    promoted, reasons = evidence.should_promote(baseline, candidate)
    assert promoted is True
    assert reasons == []

    regressed = {**candidate, "calibration_error": 0.21}
    promoted, reasons = evidence.should_promote(baseline, regressed)
    assert promoted is False
    assert "calibration_error_worse" in reasons


def test_all_zero_rare_count_targets_use_stable_constant_models() -> None:
    rows = []
    for index in range(2):
        row = {
            **{column: 0.0 for column in evidence.INDIVIDUAL_FEATURE_COLUMNS},
            **{column: 0.0 for column in evidence.COUNT_TARGETS},
            "participated": 1,
            "minutes": 90.0,
            "rating": 6.5,
            "passes_accuracy": 80.0,
        }
        row["net_impact"] = float(index)
        rows.append(row)
    models = evidence._fit_individual_models(pd.DataFrame(rows))
    predictions = models["cards_red"].predict(
        pd.DataFrame(rows)[evidence.INDIVIDUAL_FEATURE_COLUMNS]
    )
    assert predictions.tolist() == [0.0, 0.0]


def test_training_preserves_v1_when_candidate_is_rejected(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("LOKY_MAX_CPU_COUNT", "1")
    connection = get_connection(tmp_path / "train.sqlite")
    rows = []
    for index in range(30):
        row = {
            "snapshot_id": index,
            "match_id": f"match-{index}",
            "kickoff_at": f"2026-01-{(index % 28) + 1:02d}T00:00:00+00:00",
            "source_kind": "reconstructed",
            "home_goals": index % 3,
            "away_goals": (index + 1) % 3,
            "outcome": ("home", "draw", "away")[index % 3],
        }
        row.update({column: float(index % 5) for column in evidence.PLAYER_MODEL_FEATURE_COLUMNS})
        rows.append(row)
    dataset = pd.DataFrame(rows).sort_values(["kickoff_at", "match_id"]).reset_index(drop=True)
    baseline = {
        "goal_mae": 1.0,
        "goal_poisson_deviance": 1.0,
        "log_loss": 1.0,
        "brier_score": 0.5,
        "calibration_error": 0.1,
    }
    candidate = {**baseline, "goal_mae": 1.1}
    monkeypatch.setattr(evidence, "_match_dataset", lambda _: dataset)
    monkeypatch.setattr(evidence, "_individual_dataset", lambda _: pd.DataFrame())
    monkeypatch.setattr(
        evidence,
        "_evaluate_feature_set",
        lambda frame, features: baseline if features == evidence.BASELINE_FEATURE_COLUMNS else candidate,
    )
    monkeypatch.setattr(evidence, "_write_report", lambda release_dir, metadata: release_dir / "report.md")

    summary = evidence.train_player_evidence(
        connection,
        releases_dir=tmp_path / "releases",
        min_matches=30,
        min_new_matches=5,
    )

    assert summary["trained"] is True
    assert summary["promoted"] is False
    assert not (tmp_path / "releases" / "active.json").exists()


def test_manifest_failure_restores_previous_active_release(
    tmp_path: Path,
    monkeypatch,
) -> None:
    connection = get_connection(tmp_path / "activation.sqlite")
    for release_id in ("previous", "candidate"):
        release_dir = tmp_path / release_id
        release_dir.mkdir()
        (release_dir / "models.pkl").write_bytes(b"models")
        (release_dir / "metadata.json").write_text("{}", encoding="utf-8")
        register_model_release(
            connection,
            {
                "release_id": release_id,
                "release_path": str(release_dir),
                "status": "candidate",
                "model_version": evidence.MODEL_VERSION,
                "dataset_hash": release_id,
                "cutoff_at": "2026-06-13",
                "training_matches": 30,
                "live_matches": 0,
                "metrics": {},
            },
        )
    activate_db_release(connection, "previous")
    monkeypatch.setattr(
        evidence.os,
        "replace",
        lambda *args: (_ for _ in ()).throw(OSError("disk")),
    )

    try:
        evidence.activate_model_release(
            connection,
            "candidate",
            releases_dir=tmp_path / "releases",
        )
    except OSError:
        pass

    assert get_active_model_release(connection)["release_id"] == "previous"
