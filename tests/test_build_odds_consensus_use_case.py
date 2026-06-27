from __future__ import annotations

import sqlite3

from quiniela.adapters.outbound.sqlite import SQLiteOddsConsensusGateway
from quiniela.application.use_cases.build_odds_consensus import (
    BuildOddsConsensusCommand,
    BuildOddsConsensusUseCase,
)
from quiniela.infrastructure.competition_config import resolve_competition_context
from quiniela.ports.odds_consensus import OddsConsensusCounts


class FakeGateway:
    def __init__(self) -> None:
        self.calls: list[tuple[str | None, str | None]] = []

    def build(
        self,
        *,
        date: str | None = None,
        fixture_id: str | None = None,
    ) -> OddsConsensusCounts:
        self.calls.append((date, fixture_id))
        return OddsConsensusCounts(snapshots=5, consensus_rows=3)


def test_build_odds_consensus_use_case_delegates_to_gateway() -> None:
    gateway = FakeGateway()
    use_case = BuildOddsConsensusUseCase(gateway=gateway)

    result = use_case.execute(
        BuildOddsConsensusCommand(date="2026-06-18", fixture_id="12345")
    )

    assert result.as_dict() == {"snapshots": 5, "consensus_rows": 3}
    assert gateway.calls == [("2026-06-18", "12345")]


def test_build_odds_consensus_use_case_does_not_require_typer_import() -> None:
    import quiniela.application.use_cases.build_odds_consensus as module

    assert "typer" not in module.__dict__


def test_sqlite_gateway_preserves_legacy_builder_scope() -> None:
    connection = sqlite3.connect(":memory:")
    context = resolve_competition_context("champions_league_2026_2027")
    calls: list[dict[str, object]] = []

    def builder(received_connection, **kwargs):
        calls.append({"connection": received_connection, **kwargs})
        return {"snapshots": 7, "consensus_rows": 4}

    gateway = SQLiteOddsConsensusGateway(connection, context, builder=builder)

    result = gateway.build(date="2026-09-15", fixture_id="9001")

    assert result == OddsConsensusCounts(snapshots=7, consensus_rows=4)
    assert calls == [
        {
            "connection": connection,
            "date_str": "2026-09-15",
            "fixture_id": "9001",
            "competition_context": context,
        }
    ]
