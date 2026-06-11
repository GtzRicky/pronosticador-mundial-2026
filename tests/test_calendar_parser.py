from pathlib import Path

from quiniela.calendar_parser import parse_calendar_file


def test_calendar_parser_extracts_72_group_matches() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    df = parse_calendar_file(repo_root / "data" / "raw" / "calendario_mundial.md")
    assert len(df) == 72


def test_calendar_parser_priority_matches_and_timezone() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    df = parse_calendar_file(repo_root / "data" / "raw" / "calendario_mundial.md")
    mexico = df[(df["home_team"] == "México") & (df["away_team"] == "Sudáfrica")].iloc[0]
    korea = df[(df["home_team"] == "República de Corea") & (df["away_team"] == "República Checa")].iloc[0]
    assert mexico["date_cdmx"] == "2026-06-11"
    assert mexico["time_cdmx"] == "13:00"
    assert korea["date_cdmx"] == "2026-06-11"
