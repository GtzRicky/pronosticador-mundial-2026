import json
from pathlib import Path

import pandas as pd

from quiniela.db import (
    claim_automation_run,
    finish_automation_run,
    get_connection,
    recover_stale_automation_runs,
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

    for table_name in {
        "odds_market_snapshots",
        "odds_market_consensus",
        "odds_model_predictions",
    }:
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table_name,),
        ).fetchone()


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


def test_finish_automation_run_serializes_dataframe_details(tmp_path: Path) -> None:
    connection = get_connection(tmp_path / "automation-dataframe.sqlite")
    run_key = "daily:2026-06-24"
    claim_automation_run(
        connection,
        run_key=run_key,
        action="daily",
        scheduled_for="2026-06-24T00:00:00-06:00",
    )

    finish_automation_run(
        connection,
        run_key=run_key,
        status="completed",
        details={
            "predictions": pd.DataFrame(
                [{"match_id": "match-1", "home_goals": 2, "away_goals": 0}]
            )
        },
    )

    row = connection.execute(
        "SELECT status, details_json FROM automation_runs WHERE run_key = ?",
        (run_key,),
    ).fetchone()
    details = json.loads(row["details_json"])
    assert row["status"] == "completed"
    assert details["predictions"] == [
        {"match_id": "match-1", "home_goals": 2, "away_goals": 0}
    ]


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


def test_recover_stale_automation_runs_marks_old_running_rows_failed(tmp_path: Path) -> None:
    connection = get_connection(tmp_path / "automation-recover.sqlite")
    claim_automation_run(
        connection,
        run_key="hourly:2026-06-13T10",
        action="hourly",
        scheduled_for="2026-06-13T10:00:00-06:00",
        details={"attempt": 1},
    )
    claim_automation_run(
        connection,
        run_key="hourly:2026-06-13T11",
        action="hourly",
        scheduled_for="2026-06-13T11:00:00-06:00",
        details={"attempt": 1},
    )
    with connection:
        connection.execute(
            """
            UPDATE automation_runs
            SET started_at = datetime('now', '-31 minutes')
            WHERE run_key = 'hourly:2026-06-13T10'
            """
        )

    dry_run = recover_stale_automation_runs(connection, apply=False)
    applied = recover_stale_automation_runs(connection, apply=True)
    rows = connection.execute(
        "SELECT run_key, status, details_json FROM automation_runs ORDER BY run_key"
    ).fetchall()

    assert dry_run["recovered"] == 1
    assert applied["recovered"] == 1
    assert rows[0]["status"] == "failed"
    assert "stale_automation_run_recovered" in rows[0]["details_json"]
    assert rows[1]["status"] == "running"
