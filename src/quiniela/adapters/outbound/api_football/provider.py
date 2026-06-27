from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from quiniela.api_football_client import APIFootballClient
from quiniela.ports.football_data_provider import FootballDataPayload


class APIFootballClientLike(Protocol):
    def requests_remaining(self) -> int:
        """Return remaining API requests."""

    def get_fixtures(self, **params: Any) -> dict[str, Any]:
        """Return raw fixtures payload."""

    def get_fixture_by_id(self, fixture_id: int | str) -> dict[str, Any]:
        """Return raw fixture payload."""

    def get_fixture_lineups(self, fixture_id: int | str) -> dict[str, Any]:
        """Return raw lineup payload."""

    def get_fixture_statistics(self, fixture_id: int | str) -> dict[str, Any]:
        """Return raw statistics payload."""

    def get_fixture_players(self, fixture_id: int | str) -> dict[str, Any]:
        """Return raw player statistics payload."""

    def get_odds(self, fixture_id: int | str) -> dict[str, Any]:
        """Return raw odds payload."""

    def get_odds_by_date(
        self,
        date: str,
        *,
        league: int | str = 1,
        season: int | str | None = None,
    ) -> dict[str, Any]:
        """Return raw odds payload by date."""

    def get_team_fixtures(self, team_id: int | str, from_date: str, to_date: str) -> dict[str, Any]:
        """Return raw team fixtures payload."""

    def get_players(self, **params: Any) -> dict[str, Any]:
        """Return raw players payload."""

    def get_player_seasons(self, player_id: int | str) -> dict[str, Any]:
        """Return raw player seasons payload."""


class APIFootballProvider:
    def __init__(self, client: APIFootballClientLike | None = None) -> None:
        self.client = client or APIFootballClient()

    def requests_remaining(self) -> int:
        return self.client.requests_remaining()

    def get_fixtures(self, **params: Any) -> FootballDataPayload:
        return _to_payload(self.client.get_fixtures(**params))

    def get_fixture_by_id(self, fixture_id: int | str) -> FootballDataPayload:
        return _to_payload(self.client.get_fixture_by_id(fixture_id))

    def get_fixture_lineups(self, fixture_id: int | str) -> FootballDataPayload:
        return _to_payload(self.client.get_fixture_lineups(fixture_id))

    def get_fixture_statistics(self, fixture_id: int | str) -> FootballDataPayload:
        return _to_payload(self.client.get_fixture_statistics(fixture_id))

    def get_fixture_players(self, fixture_id: int | str) -> FootballDataPayload:
        return _to_payload(self.client.get_fixture_players(fixture_id))

    def get_odds(self, fixture_id: int | str) -> FootballDataPayload:
        return _to_payload(self.client.get_odds(fixture_id))

    def get_odds_by_date(
        self,
        date: str,
        *,
        league: int | str = 1,
        season: int | str | None = None,
    ) -> FootballDataPayload:
        return _to_payload(self.client.get_odds_by_date(date, league=league, season=season))

    def get_team_fixtures(self, team_id: int | str, from_date: str, to_date: str) -> FootballDataPayload:
        return _to_payload(self.client.get_team_fixtures(team_id, from_date, to_date))

    def get_players(self, **params: Any) -> FootballDataPayload:
        return _to_payload(self.client.get_players(**params))

    def get_player_seasons(self, player_id: int | str) -> FootballDataPayload:
        return _to_payload(self.client.get_player_seasons(player_id))


def _to_payload(payload: Mapping[str, Any]) -> FootballDataPayload:
    response_raw = payload.get("response") or []
    response: tuple[Mapping[str, Any], ...]
    if isinstance(response_raw, list):
        response = tuple(item for item in response_raw if isinstance(item, Mapping))
    else:
        response = ()
    results = payload.get("results")
    return FootballDataPayload(
        response=response,
        results=int(results) if results is not None else len(response),
        errors=payload.get("errors"),
        paging=payload.get("paging") if isinstance(payload.get("paging"), Mapping) else None,
        parameters=payload.get("parameters") if isinstance(payload.get("parameters"), Mapping) else None,
    )
