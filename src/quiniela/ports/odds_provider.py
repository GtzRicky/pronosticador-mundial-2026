from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from quiniela.domain.odds import MarketOdds, MarketProbabilities


@dataclass(frozen=True)
class OddsPayload:
    response: tuple[Mapping[str, Any], ...]
    results: int
    errors: Any = None
    paging: Mapping[str, Any] | None = None
    parameters: Mapping[str, Any] | None = None


class OddsProvider(Protocol):
    def get_odds(self, fixture_id: int | str) -> OddsPayload:
        """Return provider odds for one fixture."""

    def get_odds_by_date(
        self,
        date: str,
        *,
        league: int | str = 1,
        season: int | str | None = None,
    ) -> OddsPayload:
        """Return provider odds for one date and competition context."""


class MarketProbabilityConverter(Protocol):
    def to_probabilities(self, market: MarketOdds) -> MarketProbabilities:
        """Convert priced market odds into normalized market probabilities."""
