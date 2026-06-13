from datetime import datetime
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from quiniela.db import get_connection
from quiniela.player_model import infer_team_sheet
from quiniela.web_lineup_fallback import NewsArticle, refresh_web_lineup_fallback


TZ = ZoneInfo("America/Mexico_City")


class FakeNewsClient:
    def __init__(self, articles: list[NewsArticle]) -> None:
        self.articles = articles
        self.calls = 0

    def search(self, query: str, max_articles: int = 6) -> list[NewsArticle]:
        self.calls += 1
        return self.articles[:max_articles]


def _seed_match_and_roster(connection) -> None:
    connection.execute(
        """
        INSERT INTO matches (
            match_id, date_et, time_et, datetime_et, date_cdmx, time_cdmx,
            datetime_cdmx, home_team, away_team, home_team_norm, away_team_norm,
            group_name, stadium, stage, status, api_fixture_id
        ) VALUES (
            'match-1', '2026-06-13', '15:00', '2026-06-13T15:00:00-04:00',
            '2026-06-13', '13:00', '2026-06-13T13:00:00-06:00',
            'Catar', 'Suiza', 'qatar', 'switzerland', 'B', 'Estadio',
            'group', 'NS', 1489373
        )
        """
    )
    positions = ["goalkeeper", "defender", "defender", "defender", "defender",
                 "midfielder", "midfielder", "midfielder", "forward", "forward",
                 "forward", "midfielder"]
    for index, position in enumerate(positions, start=1):
        name = f"Swiss Player {index}"
        connection.execute(
            """
            INSERT INTO players (
                team, team_norm, player, player_norm, position_group,
                api_player_id, api_player_name, api_position
            ) VALUES (?, 'switzerland', ?, ?, ?, ?, ?, ?)
            """,
            (
                "Suiza",
                name,
                f"swiss_player_{index}",
                position,
                1000 + index,
                name,
                {
                    "goalkeeper": "G",
                    "defender": "D",
                    "midfielder": "M",
                    "forward": "F",
                }[position],
            ),
        )
    connection.commit()


def test_fallback_builds_auditable_estimate_and_model_uses_it(tmp_path: Path) -> None:
    connection = get_connection(tmp_path / "fallback.sqlite")
    _seed_match_and_roster(connection)
    article = NewsArticle(
        title="Qatar v Switzerland predicted lineup",
        url="https://example.com/swiss-lineup",
        published_at="2026-06-13T17:00:00+00:00",
        text=(
            "Predicted lineup starting XI: Swiss Player 1, Swiss Player 2, "
            "Swiss Player 3, Swiss Player 4, Swiss Player 5, Swiss Player 6, "
            "Swiss Player 7, Swiss Player 8, Swiss Player 9, Swiss Player 10, "
            "Swiss Player 11."
        ),
    )
    client = FakeNewsClient([article])

    result = refresh_web_lineup_fallback(
        "match-1",
        window_label="t-30",
        connection=connection,
        now=datetime(2026, 6, 13, 12, 30, tzinfo=TZ),
        client=client,
    )

    assert result["status"] == "completed"
    team_result = next(row for row in result["teams"] if row["team"] == "Suiza")
    assert team_result["status"] == "created"
    row = connection.execute(
        "SELECT * FROM lineup_estimates WHERE team_norm = 'switzerland'"
    ).fetchone()
    payload = json.loads(row["source_json"])
    assert len(payload["startXI"]) == 11
    assert payload["_lineup_source"] == "web_estimated"
    assert payload["_direct_matches"] >= 4
    assert payload["_sources"][0]["url"] == article.url

    sheet = infer_team_sheet(
        connection,
        "switzerland",
        "2026-06-13T13:00:00-06:00",
        fixture_id=1489373,
    )
    assert sheet["source"] == "web_estimated_lineup"
    assert len(sheet["starters"]) == 11


def test_complete_official_lineup_prevents_web_search(tmp_path: Path) -> None:
    connection = get_connection(tmp_path / "official.sqlite")
    _seed_match_and_roster(connection)
    connection.execute(
        "UPDATE matches SET away_team_norm = 'suiza' WHERE match_id = 'match-1'"
    )
    payload = {
        "startXI": [
            {"player": {"id": index, "name": f"Official {index}", "pos": "M"}}
            for index in range(1, 12)
        ]
    }
    for team_norm in ("qatar", "switzerland"):
        connection.execute(
            """
            INSERT INTO historical_lineups (fixture_id, team_norm, source_json)
            VALUES ('1489373', ?, ?)
            """,
            (team_norm, json.dumps(payload)),
        )
    connection.commit()
    client = FakeNewsClient([])

    result = refresh_web_lineup_fallback(
        "match-1",
        window_label="t-30",
        connection=connection,
        now=datetime(2026, 6, 13, 12, 30, tzinfo=TZ),
        client=client,
    )

    assert result["status"] == "official_lineups_available"
    assert client.calls == 0


def test_fallback_does_not_search_before_t_minus_thirty(tmp_path: Path) -> None:
    connection = get_connection(tmp_path / "outside-window.sqlite")
    _seed_match_and_roster(connection)
    client = FakeNewsClient([])

    result = refresh_web_lineup_fallback(
        "match-1",
        window_label="t-60",
        connection=connection,
        now=datetime(2026, 6, 13, 12, 0, tzinfo=TZ),
        client=client,
    )

    assert result["status"] == "outside_window"
    assert client.calls == 0
