from pathlib import Path

import numpy as np

from quiniela.db import get_connection
from quiniela.odds_loader import (
    build_odds_consensus,
    odds_adjusted_score_prediction,
    odds_consensus_summary,
    store_odds_payload,
)


def _seed_match(connection) -> None:
    connection.execute(
        """
        INSERT INTO matches (
            match_id, date_et, time_et, datetime_et,
            date_cdmx, time_cdmx, datetime_cdmx,
            home_team, away_team, home_team_norm, away_team_norm,
            group_name, stadium, stage, status, api_fixture_id
        ) VALUES (
            'match-odds', '2026-06-18', '12:00', '2026-06-18T12:00:00-04:00',
            '2026-06-18', '10:00', '2026-06-18T10:00:00-06:00',
            'Portugal', 'RD Congo', 'portugal', 'dr_congo',
            'K', 'Estadio', 'group', 'NS', 12345
        )
        """
    )
    connection.commit()


def _odds_payload() -> dict:
    return {
        "response": [
            {
                "fixture": {"id": 12345},
                "league": {"id": 1, "season": 2026},
                "update": "2026-06-18T12:00:00+00:00",
                "bookmakers": [
                    {
                        "id": 8,
                        "name": "Bet365",
                        "bets": [
                            {
                                "id": 1,
                                "name": "Match Winner",
                                "values": [
                                    {"value": "Home", "odd": "1.50"},
                                    {"value": "Draw", "odd": "4.00"},
                                    {"value": "Away", "odd": "7.00"},
                                ],
                            },
                            {
                                "id": 5,
                                "name": "Goals Over/Under",
                                "values": [
                                    {"value": "Over 2.5", "odd": "1.80"},
                                    {"value": "Under 2.5", "odd": "2.00"},
                                ],
                            },
                            {
                                "id": 8,
                                "name": "Both Teams Score",
                                "values": [
                                    {"value": "Yes", "odd": "2.20"},
                                    {"value": "No", "odd": "1.65"},
                                ],
                            },
                            {
                                "id": 10,
                                "name": "Exact Score",
                                "values": [
                                    {"value": "2-0", "odd": "7.00"},
                                    {"value": "1-0", "odd": "6.00"},
                                    {"value": "1-1", "odd": "8.00"},
                                ],
                            },
                        ],
                    },
                    {
                        "id": 4,
                        "name": "Pinnacle",
                        "bets": [
                            {
                                "id": 1,
                                "name": "Match Winner",
                                "values": [
                                    {"value": "Home", "odd": "1.55"},
                                    {"value": "Draw", "odd": "3.90"},
                                    {"value": "Away", "odd": "6.80"},
                                ],
                            }
                        ],
                    },
                ],
            }
        ]
    }


def test_store_odds_payload_normalizes_markets_and_consensus(tmp_path: Path) -> None:
    connection = get_connection(tmp_path / "odds.sqlite")
    _seed_match(connection)

    stored = store_odds_payload(connection, _odds_payload())
    duplicate = store_odds_payload(connection, _odds_payload())
    consensus = build_odds_consensus(connection, date_str="2026-06-18")
    summary = odds_consensus_summary(connection, "12345")

    assert stored["inserted"] > 0
    assert duplicate["inserted"] == 0
    assert consensus["consensus_rows"] > 0
    assert "match_winner" in summary["markets"]
    one_x_two = summary["markets"]["match_winner"]
    assert set(one_x_two) == {"home", "draw", "away"}
    assert abs(sum(item["probability"] for item in one_x_two.values()) - 1.0) < 1e-6


def test_odds_adjusted_score_prediction_uses_market_constraints(tmp_path: Path) -> None:
    connection = get_connection(tmp_path / "odds-score.sqlite")
    _seed_match(connection)
    store_odds_payload(connection, _odds_payload())
    build_odds_consensus(connection, fixture_id="12345")
    poisson = np.ones((5, 5), dtype=float)
    poisson = poisson / poisson.sum()

    result = odds_adjusted_score_prediction(
        connection,
        fixture_id=12345,
        poisson_matrix=poisson,
    )

    assert result is not None
    assert result["score"] in {"2-0", "1-0", "1-1"}
    assert 0.0 < result["probability"] < 1.0
    row = connection.execute(
        "SELECT market_key, prediction_key FROM odds_model_predictions"
    ).fetchone()
    assert row["market_key"] == "exact_score"
