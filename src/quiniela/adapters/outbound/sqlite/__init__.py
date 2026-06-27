"""SQLite outbound adapter helpers."""

from quiniela.adapters.outbound.sqlite.connection import connect_sqlite
from quiniela.adapters.outbound.sqlite.doctor_health_repository import SQLiteDoctorHealthRepository
from quiniela.adapters.outbound.sqlite.match_repository import SQLiteMatchRepository
from quiniela.adapters.outbound.sqlite.migrations import apply_schema
from quiniela.adapters.outbound.sqlite.odds_consensus_gateway import SQLiteOddsConsensusGateway
from quiniela.adapters.outbound.sqlite.prediction_repository import SQLitePredictionRepository
from quiniela.adapters.outbound.sqlite.snapshot_repository import SQLiteSnapshotRepository

__all__ = [
    "SQLiteDoctorHealthRepository",
    "SQLiteMatchRepository",
    "SQLiteOddsConsensusGateway",
    "SQLitePredictionRepository",
    "SQLiteSnapshotRepository",
    "apply_schema",
    "connect_sqlite",
]
