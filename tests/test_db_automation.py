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

    notification_columns = {
        row["name"]
        for row in connection.execute(
            "PRAGMA table_info(notification_deliveries)"
        ).fetchall()
    }
    assert {
        "match_id",
        "kickoff_at",
        "window_label",
        "channel",
        "notification_type",
        "team_norm",
        "lineup_hash",
        "prediction_id",
        "status",
        "attempt_count",
        "next_attempt_at",
    }.issubset(notification_columns)


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


def test_claim_automation_run_recovers_stale_running_claim(tmp_path: Path) -> None:
    connection = get_connection(tmp_path / "automation-stale.sqlite")
    run_key = "daily:2026-06-13"

    claimed = claim_automation_run(
        connection,
        run_key=run_key,
        action="daily",
        scheduled_for="2026-06-13T00:00:00-06:00",
        details={"attempt": 1},
    )
    fresh_duplicate = claim_automation_run(
        connection,
        run_key=run_key,
        action="daily",
        scheduled_for="2026-06-13T00:00:00-06:00",
        details={"attempt": 2},
    )
    with connection:
        connection.execute(
            """
            UPDATE automation_runs
            SET started_at = datetime('now', '-31 minutes')
            WHERE run_key = ?
            """,
            (run_key,),
        )

    reclaimed = claim_automation_run(
        connection,
        run_key=run_key,
        action="daily",
        scheduled_for="2026-06-13T00:00:00-06:00",
        details={"attempt": 3},
        stale_after_minutes=25,
    )
    row = connection.execute(
        "SELECT status, details_json, finished_at FROM automation_runs WHERE run_key = ?",
        (run_key,),
    ).fetchone()

    assert claimed is True
    assert fresh_duplicate is False
    assert reclaimed is True
    assert row["status"] == "running"
    assert '"attempt": 3' in row["details_json"]
    assert row["finished_at"] is None
