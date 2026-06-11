from pathlib import Path

from quiniela.name_maps import normalize_team_name, preferred_team_search_name
from quiniela.roster_parser import parse_roster_file


def test_roster_parser_extracts_priority_teams() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    df = parse_roster_file(repo_root / "data" / "raw" / "seleccionados_mundialistas.md")
    teams = set(df["team"].unique())
    assert "México" in teams
    assert "Sudáfrica" in teams
    assert "República de Corea" in teams
    assert "Chequia" in teams


def test_roster_parser_maps_aliases_and_coach() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    df = parse_roster_file(repo_root / "data" / "raw" / "seleccionados_mundialistas.md")
    korea_rows = df[df["team"] == "República de Corea"]
    czech_rows = df[df["team"] == "Chequia"]
    assert (korea_rows["team_norm"] == "south_korea").all()
    assert (czech_rows["team_norm"] == "czech_republic").all()
    assert "Javier Aguirre" in set(df[df["team"] == "México"]["coach"].dropna())


def test_name_normalization_tolerates_mojibake_and_console_damage() -> None:
    assert normalize_team_name("M?xico") == "mexico"
    assert normalize_team_name("Rep?blica de Corea") == "south_korea"
    assert preferred_team_search_name("M?xico") == "Mexico"
