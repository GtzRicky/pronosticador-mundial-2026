from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class FootballDataPayload:
    response: tuple[Mapping[str, Any], ...]
    results: int
    errors: Any = None
    paging: Mapping[str, Any] | None = None
    parameters: Mapping[str, Any] | None = None


class FootballDataProvider(Protocol):
    def requests_remaining(self) -> int:
        """Return provider requests remaining for the current quota window."""

    def get_fixtures(self, **params: Any) -> FootballDataPayload:
        """Return fixture data for provider-specific query params."""

    def get_fixture_by_id(self, fixture_id: int | str) -> FootballDataPayload:
        """Return one fixture by provider id."""

    def get_fixture_lineups(self, fixture_id: int | str) -> FootballDataPayload:
        """Return lineups for one fixture."""

    def get_fixture_statistics(self, fixture_id: int | str) -> FootballDataPayload:
        """Return team statistics for one fixture."""

    def get_fixture_players(self, fixture_id: int | str) -> FootballDataPayload:
        """Return player statistics for one fixture."""

    def get_odds(self, fixture_id: int | str) -> FootballDataPayload:
        """Return odds for one fixture."""

    def get_odds_by_date(
        self,
        date: str,
        *,
        league: int | str = 1,
        season: int | str | None = None,
    ) -> FootballDataPayload:
        """Return odds for one date and competition context."""

    def get_team_fixtures(self, team_id: int | str, from_date: str, to_date: str) -> FootballDataPayload:
        """Return fixtures for one team in a date range."""

    def get_players(self, **params: Any) -> FootballDataPayload:
        """Return player data for provider-specific query params."""

    def get_player_seasons(self, player_id: int | str) -> FootballDataPayload:
        """Return seasons available for one player."""
