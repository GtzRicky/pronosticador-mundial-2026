from pathlib import Path

import pytest

from quiniela.name_maps import (
    UnknownTeamNameError,
    normalize_team_name,
    preferred_team_search_name,
    require_known_team_name,
)
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
    assert normalize_team_name("Pa?ses Bajos") == "netherlands"
    assert normalize_team_name("Cura?ao") == "curacao"
    assert preferred_team_search_name("M?xico") == "Mexico"


def test_name_normalization_maps_world_cup_aliases_without_false_matches() -> None:
    expected = {
        "Alemania": "germany",
        "Arabia Saudí": "saudi_arabia",
        "Argelia": "algeria",
        "Germany": "germany",
        "Argentina": "argentina",
        "Australia": "australia",
        "Austria": "austria",
        "Bosnia y Herzegovina": "bosnia_herzegovina",
        "Brasil": "brazil",
        "Bélgica": "belgium",
        "Cabo Verde": "cape_verde",
        "Canadá": "canada",
        "Catar": "qatar",
        "Colombia": "colombia",
        "Costa de Marfil": "ivory_coast",
        "Croacia": "croatia",
        "Curaçao": "curacao",
        "Curazao": "curacao",
        "Ecuador": "ecuador",
        "Egipto": "egypt",
        "Escocia": "scotland",
        "España": "spain",
        "Estados Unidos": "usa",
        "Francia": "france",
        "Ghana": "ghana",
        "Haití": "haiti",
        "Inglaterra": "england",
        "Irak": "iraq",
        "Irán": "iran",
        "RI de Irán": "iran",
        "Japón": "japan",
        "Jordania": "jordan",
        "Marruecos": "morocco",
        "Netherlands": "netherlands",
        "Noruega": "norway",
        "Nueva Zelanda": "new_zealand",
        "Panamá": "panama",
        "Países Bajos": "netherlands",
        "Paraguay": "paraguay",
        "Portugal": "portugal",
        "Congo DR": "dr_congo",
        "RD Congo": "dr_congo",
        "República Checa": "czech_republic",
        "República de Corea": "south_korea",
        "Senegal": "senegal",
        "Sudáfrica": "south_africa",
        "Suecia": "sweden",
        "Suiza": "switzerland",
        "Turquía": "turkey",
        "Túnez": "tunisia",
        "Uruguay": "uruguay",
        "Uzbekistán": "uzbekistan",
    }
    for source, target in expected.items():
        assert normalize_team_name(source) == target


def test_strict_team_normalization_rejects_unknown_names() -> None:
    with pytest.raises(UnknownTeamNameError):
        require_known_team_name("Team Atlantis", context="live fixture home team")
