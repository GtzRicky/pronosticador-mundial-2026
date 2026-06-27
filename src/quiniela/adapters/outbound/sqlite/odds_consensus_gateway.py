from __future__ import annotations

from collections.abc import Callable
import sqlite3

from quiniela.infrastructure.competition_config import CompetitionContext
from quiniela.ports.odds_consensus import OddsConsensusCounts


LegacyConsensusBuilder = Callable[..., dict[str, int]]


class SQLiteOddsConsensusGateway:
    def __init__(
        self,
        connection: sqlite3.Connection,
        competition_context: CompetitionContext,
        builder: LegacyConsensusBuilder | None = None,
    ) -> None:
        self._connection = connection
        self._competition_context = competition_context
        self._builder = builder

    @property
    def builder(self) -> LegacyConsensusBuilder:
        if self._builder is None:
            from quiniela.odds_loader import build_odds_consensus

            self._builder = build_odds_consensus
        return self._builder

    def build(
        self,
        *,
        date: str | None = None,
        fixture_id: str | None = None,
    ) -> OddsConsensusCounts:
        result = self.builder(
            self._connection,
            date_str=date,
            fixture_id=fixture_id,
            competition_context=self._competition_context,
        )
        return OddsConsensusCounts(
            snapshots=int(result.get("snapshots", 0)),
            consensus_rows=int(result.get("consensus_rows", 0)),
        )
