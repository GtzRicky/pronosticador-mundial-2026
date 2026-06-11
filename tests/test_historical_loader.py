from pathlib import Path

from quiniela.historical_loader import write_backfill_report


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
