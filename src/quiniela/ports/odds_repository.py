from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from quiniela.domain.odds import MarketOdds, MarketProbabilities
from quiniela.domain.value_objects import MatchId


@dataclass(frozen=True)
class OddsSnapshotRecord:
    fixture_id: str
    market: MarketOdds
    captured_at: datetime | None = None
    match_id: MatchId | None = None


@dataclass(frozen=True)
class OddsConsensusRecord:
    fixture_id: str
    market: MarketProbabilities
    calculated_at: datetime | None = None
    match_id: MatchId | None = None


class OddsRepository(Protocol):
    def save_snapshots(self, snapshots: list[OddsSnapshotRecord]) -> int:
        """Persist odds market snapshots and return inserted count."""

    def list_snapshots(
        self,
        *,
        date_cdmx: str | None = None,
        fixture_id: str | None = None,
    ) -> list[OddsSnapshotRecord]:
        """Return stored snapshots for a date or fixture."""

    def save_consensus(self, consensus: list[OddsConsensusRecord]) -> int:
        """Persist consensus probabilities and return upserted count."""

    def list_consensus(self, fixture_id: str) -> list[OddsConsensusRecord]:
        """Return consensus probabilities for one fixture."""
