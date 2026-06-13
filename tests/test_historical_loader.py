from pathlib import Path

from quiniela.db import get_connection
from quiniela.historical_loader import sync_finished_results_for_date, write_backfill_report


def test_write_backfill_report_includes_strategy_section(tmp_path: Path) -> None:
    output_path = tmp_path / "data_quality_report.md"
    report = {
        "teams_requested": ["México"],
        "teams_resolved": ["México"],
        "teams_missing": [],
        "fixtures_stored": 3,
        "lineups_stored": 6,
        "statistics_stored": 6,
        "events_stored": 20,
        "odds_stored": 0,
        "missing_fixture_counts": {"México": 2},
        "team_history_strategy": {"México": "free_plan_season_fallback:plan limit"},
    }
    write_backfill_report(report, output_path)
    text = output_path.read_text(encoding="utf-8")
    assert "## Strategy by team" in text
    assert "México: free_plan_season_fallback:plan limit" in text


def test_sync_finished_results_for_date_only_copies_final_matches(tmp_path: Path) -> None:
    connection = get_connection(tmp_path / "results.sqlite")
    with connection:
        connection.execute(
            """
            INSERT INTO matches (
                match_id, date_et, time_et, datetime_et,
                date_cdmx, time_cdmx, datetime_cdmx,
                home_team, away_team, home_team_norm, away_team_norm,
                group_name, stadium, stage, status, api_fixture_id
            ) VALUES (
                'match-final', '2026-06-13', '19:00', '2026-06-13T19:00:00-04:00',
                '2026-06-13', '17:00', '2026-06-13T17:00:00-06:00',
                'Local', 'Visita', 'local', 'visita',
                'A', 'Estadio', 'group', 'FT', 100
            )
            """
        )
        connection.execute(
            """
            INSERT INTO historical_matches (
                fixture_id, home_goals, away_goals, status, source_json
            ) VALUES ('100', 2, 1, 'FT', '{"fixture": {"id": 100}}')
            """
        )

    summary = sync_finished_results_for_date("2026-06-13", connection=connection)
    row = connection.execute(
        "SELECT home_goals, away_goals FROM actual_results WHERE match_id = 'match-final'"
    ).fetchone()

    assert summary == {
        "updated": 1,
        "match_ids": ["match-final"],
        "new_match_ids": ["match-final"],
    }
    assert (row["home_goals"], row["away_goals"]) == (2, 1)

    repeated = sync_finished_results_for_date("2026-06-13", connection=connection)
    assert repeated["match_ids"] == ["match-final"]
    assert repeated["new_match_ids"] == []
