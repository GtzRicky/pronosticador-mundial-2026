from __future__ import annotations

from typing import Any

from quiniela.adapters.outbound.api_football.provider import APIFootballProvider
from quiniela.ports.football_data_provider import FootballDataPayload


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    def requests_remaining(self) -> int:
        self.calls.append(("requests_remaining", (), {}))
        return 42

    def get_fixtures(self, **params: Any) -> dict[str, Any]:
        self.calls.append(("get_fixtures", (), params))
        return {
            "response": [{"fixture": {"id": 123}}],
            "results": 1,
            "errors": [],
            "paging": {"current": 1},
        }

    def get_fixture_by_id(self, fixture_id: int | str) -> dict[str, Any]:
        self.calls.append(("get_fixture_by_id", (fixture_id,), {}))
        return {"response": [{"fixture": {"id": fixture_id}}], "results": 1}

    def get_fixture_lineups(self, fixture_id: int | str) -> dict[str, Any]:
        self.calls.append(("get_fixture_lineups", (fixture_id,), {}))
        return {"response": [{"team": {"id": 1}}], "results": 1}

    def get_fixture_statistics(self, fixture_id: int | str) -> dict[str, Any]:
        self.calls.append(("get_fixture_statistics", (fixture_id,), {}))
        return {"response": [{"statistics": []}], "results": 1}

    def get_fixture_players(self, fixture_id: int | str) -> dict[str, Any]:
        self.calls.append(("get_fixture_players", (fixture_id,), {}))
        return {"response": [{"players": []}], "results": 1}

    def get_odds(self, fixture_id: int | str) -> dict[str, Any]:
        self.calls.append(("get_odds", (fixture_id,), {}))
        return {"response": [{"bookmakers": []}], "results": 1}

    def get_odds_by_date(
        self,
        date: str,
        *,
        league: int | str = 1,
        season: int | str | None = None,
    ) -> dict[str, Any]:
        self.calls.append(("get_odds_by_date", (date,), {"league": league, "season": season}))
        return {"response": [{"fixture": {"date": date}}], "results": 1}

    def get_team_fixtures(self, team_id: int | str, from_date: str, to_date: str) -> dict[str, Any]:
        self.calls.append(("get_team_fixtures", (team_id, from_date, to_date), {}))
        return {"response": [{"team": {"id": team_id}}], "results": 1}

    def get_players(self, **params: Any) -> dict[str, Any]:
        self.calls.append(("get_players", (), params))
        return {"response": [{"player": {"id": 9}}], "results": 1}

    def get_player_seasons(self, player_id: int | str) -> dict[str, Any]:
        self.calls.append(("get_player_seasons", (player_id,), {}))
        return {"response": [{"season": 2026}], "results": 1}


def test_api_football_provider_is_reexported_from_package() -> None:
    from quiniela.adapters.outbound.api_football import APIFootballProvider as PackageProvider

    assert PackageProvider is APIFootballProvider
    assert PackageProvider(client=FakeClient()).requests_remaining() == 42


def test_api_football_provider_wraps_fixtures_payload_without_raw_dict() -> None:
    client = FakeClient()
    provider = APIFootballProvider(client=client)

    payload = provider.get_fixtures(date="2026-06-18")

    assert isinstance(payload, FootballDataPayload)
    assert payload.results == 1
    assert payload.response[0]["fixture"] == {"id": 123}
    assert payload.paging == {"current": 1}
    assert client.calls == [("get_fixtures", (), {"date": "2026-06-18"})]


def test_api_football_provider_delegates_quota_and_odds_by_date() -> None:
    client = FakeClient()
    provider = APIFootballProvider(client=client)

    remaining = provider.requests_remaining()
    payload = provider.get_odds_by_date("2026-06-18", league=1, season=2026)

    assert remaining == 42
    assert payload.response[0]["fixture"] == {"date": "2026-06-18"}
    assert client.calls == [
        ("requests_remaining", (), {}),
        ("get_odds_by_date", ("2026-06-18",), {"league": 1, "season": 2026}),
    ]


def test_api_football_provider_delegates_fixture_detail_methods() -> None:
    client = FakeClient()
    provider = APIFootballProvider(client=client)

    assert provider.get_fixture_by_id(123).response[0]["fixture"] == {"id": 123}
    assert provider.get_fixture_lineups(123).response[0]["team"] == {"id": 1}
    assert provider.get_fixture_statistics(123).response[0]["statistics"] == []
    assert provider.get_fixture_players(123).response[0]["players"] == []
    assert provider.get_odds(123).response[0]["bookmakers"] == []


def test_api_football_provider_delegates_team_and_player_methods() -> None:
    client = FakeClient()
    provider = APIFootballProvider(client=client)

    assert provider.get_team_fixtures(7, "2026-01-01", "2026-06-01").response[0]["team"] == {"id": 7}
    assert provider.get_players(team=7, season=2026).response[0]["player"] == {"id": 9}
    assert provider.get_player_seasons(9).response[0]["season"] == 2026
