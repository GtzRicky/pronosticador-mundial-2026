from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class OddsConsensusCounts:
    snapshots: int
    consensus_rows: int


class OddsConsensusGateway(Protocol):
    def build(
        self,
        *,
        date: str | None = None,
        fixture_id: str | None = None,
    ) -> OddsConsensusCounts:
        """Build and persist consensus rows for the selected odds scope."""
