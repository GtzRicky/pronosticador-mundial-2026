from datetime import datetime
from zoneinfo import ZoneInfo

from quiniela.html_report import extract_lineup_players, render_predictions_html


def test_extract_lineup_players_maps_number_and_position() -> None:
    payload = {
        "startXI": [
            {"player": {"name": "L. Malagón", "number": 1, "pos": "G"}},
            {"player": {"name": "J. Sánchez", "number": 2, "pos": "D"}},
        ]
    }

    players = extract_lineup_players(payload, "startXI")

    assert len(players) == 2
    assert players[0].name == "L. Malagón"
    assert players[0].number == "1"
    assert players[0].position == "POR"
    assert players[1].position == "DEF"


def test_render_predictions_html_shows_predicted_and_pending_actual() -> None:
    match_rows = [
        {
            "match_id": "20260611_mexico_south_africa",
            "date_cdmx": "2026-06-11",
            "time_cdmx": "13:00",
            "datetime_cdmx": "2026-06-11T13:00:00-06:00",
            "home_team": "México",
            "away_team": "Sudáfrica",
            "home_team_norm": "mexico",
            "away_team_norm": "south_africa",
            "group_name": "A",
            "stadium": "Estadio Azteca",
            "stage": "group",
            "status": "scheduled",
            "api_fixture_id": 1489369,
            "predicted_score": "1-0",
            "probability": 0.21,
            "model_version": "poisson_v1",
            "hybrid_predicted_score": "2-0",
            "hybrid_probability": 0.18,
            "home_win_probability": 0.62,
            "draw_probability": 0.23,
            "away_win_probability": 0.15,
            "outcome_model_version": "logit_outcome_v1",
            "data_freshness_at": "2026-06-11T12:59:00-06:00",
            "generated_at": "2026-06-11T10:00:00",
            "actual_home_goals": None,
            "actual_away_goals": None,
        }
    ]
    lineups = {
        ("1489369", "mexico"): {
            "formation": "4-3-3",
            "coach": {"name": "DT Local"},
            "startXI": [{"player": {"name": "Jugador 1", "number": 1, "pos": "G"}}],
            "substitutes": [{"player": {"name": "Jugador 12", "number": 12, "pos": "M"}}],
        },
        ("1489369", "south_africa"): {
            "formation": "4-4-2",
            "coach": {"name": "DT Visitante"},
            "startXI": [{"player": {"name": "Player 1", "number": 1, "pos": "G"}}],
            "substitutes": [{"player": {"name": "Player 12", "number": 12, "pos": "F"}}],
        },
    }

    impacts = {
        ("20260611_mexico_south_africa", "mexico"): [
            {"player_name": "Jugador 1", "role_bucket": "goalkeeper", "net_impact": 0.4, "attack_impact": 0.0, "defense_impact": 0.4, "discipline_impact": 0.0}
        ],
        ("20260611_mexico_south_africa", "south_africa"): [
            {"player_name": "Player 12", "role_bucket": "forward", "net_impact": 0.2, "attack_impact": 0.2, "defense_impact": 0.0, "discipline_impact": 0.0}
        ],
    }

    html = render_predictions_html(match_rows, lineups, impacts)

    assert "México" in html
    assert "Estadio Azteca" in html
    assert "1-0" in html
    assert "2-0" in html
    assert "Poisson base" in html
    assert "Híbrido Logit + Poisson" in html
    assert "62.0%" in html
    assert "logit_outcome_v1" in html
    assert "Pendiente" in html
    assert "Jugador 1" in html
    assert "Player 12" in html
    assert "Top impactos" in html


def test_render_predictions_html_shows_last_updated_in_cdmx() -> None:
    html = render_predictions_html(
        [],
        {},
        {},
        generated_at=datetime(2026, 6, 13, 18, 45, tzinfo=ZoneInfo("America/Mexico_City")),
    )

    assert "Ultima actualizacion: 2026-06-13 18:45:00 CDMX" in html


def test_render_predictions_html_labels_web_estimated_lineup() -> None:
    match_rows = [
        {
            "match_id": "match-estimated",
            "date_cdmx": "2026-06-13",
            "time_cdmx": "13:00",
            "datetime_cdmx": "2026-06-13T13:00:00-06:00",
            "home_team": "Catar",
            "away_team": "Suiza",
            "home_team_norm": "qatar",
            "away_team_norm": "suiza",
            "group_name": "B",
            "stadium": "Estadio",
            "stage": "group",
            "status": "NS",
            "api_fixture_id": 1489373,
            "predicted_score": "0-2",
            "probability": 0.2,
            "generated_at": "2026-06-13T12:30:00-06:00",
            "actual_home_goals": None,
            "actual_away_goals": None,
        }
    ]
    starter = {"player": {"name": "Jugador", "number": None, "pos": "M"}}
    lineups = {
        ("1489373", "qatar"): {
            "_lineup_source": "official",
            "startXI": [starter] * 11,
        },
        ("1489373", "switzerland"): {
            "_lineup_source": "web_estimated",
            "_confidence": 0.64,
            "_sources": [{"url": "https://example.com/lineup"}],
            "formation": "Estimada",
            "startXI": [starter] * 11,
        },
    }

    html = render_predictions_html(match_rows, lineups, {})

    assert "Alineación web estimada" in html
    assert "confianza 64%" in html
    assert 'href="https://example.com/lineup"' in html
