from __future__ import annotations

from dataclasses import dataclass

from quiniela.ports.odds_consensus import OddsConsensusGateway


@dataclass(frozen=True)
class BuildOddsConsensusCommand:
    date: str | None = None
    fixture_id: str | None = None


@dataclass(frozen=True)
class BuildOddsConsensusResult:
    snapshots: int
    consensus_rows: int

    def as_dict(self) -> dict[str, int]:
        return {
            "snapshots": self.snapshots,
            "consensus_rows": self.consensus_rows,
        }


class BuildOddsConsensusUseCase:
    def __init__(
        self,
        *,
        gateway: OddsConsensusGateway,
    ) -> None:
        self._gateway = gateway

    def execute(self, command: BuildOddsConsensusCommand) -> BuildOddsConsensusResult:
        result = self._gateway.build(
            date=command.date,
            fixture_id=command.fixture_id,
        )
        return BuildOddsConsensusResult(
            snapshots=result.snapshots,
            consensus_rows=result.consensus_rows,
        )
