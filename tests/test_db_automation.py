from pathlib import Path

from quiniela.db import (
    claim_automation_run,
    finish_automation_run,
    get_connection,
)


def test_prediction_columns_and_automation_runs_are_migrated(tmp_path: Path) -> None:
    connection = get_connection(tmp_path / "automation.sqlite")

    prediction_columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(predictions)").fetchall()
    }
    assert {
        "hybrid_predicted_score",
        "hybrid_probability",
        "home_win_probability",
        "draw_probability",
        "away_win_probability",
        "outcome_model_version",
        "data_freshness_at",
    }.issubset(prediction_columns)

    run_columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(automation_runs)").fetchall()
    }
    assert {"run_key", "action", "scheduled_for", "status"}.issubset(run_columns)


def test_claim_automation_run_is_idempotent(tmp_path: Path) -> None:
    connection = get_connection(tmp_path / "automation.sqlite")

    claimed = claim_automation_run(
        connection,
        run_key="hourly:2026-06-13T10",
        action="hourly",
        scheduled_for="2026-06-13T10:00:00-06:00",
    )
    duplicate = claim_automation_run(
        connection,
        run_key="hourly:2026-06-13T10",
        action="hourly",
        scheduled_for="2026-06-13T10:00:00-06:00",
    )
    finish_automation_run(
        connection,
        run_key="hourly:2026-06-13T10",
        status="completed",
        details={"fixtures": 3},
    )

    row = connection.execute(
        "SELECT status, details_json FROM automation_runs WHERE run_key = ?",
        ("hourly:2026-06-13T10",),
    ).fetchone()
    assert claimed is True
    assert duplicate is False
    assert row["status"] == "completed"
    assert '"fixtures": 3' in row["details_json"]
