"""Ports that define application-facing contracts."""

from quiniela.ports.clock import Clock
from quiniela.ports.doctor_health import (
    DatabaseInspection,
    DoctorHealthRepository,
    ModelReleaseInspection,
    OutboxInspection,
)
from quiniela.ports.football_data_provider import FootballDataPayload, FootballDataProvider
from quiniela.ports.odds_consensus import OddsConsensusCounts, OddsConsensusGateway
from quiniela.ports.odds_provider import (
    MarketProbabilityConverter,
    OddsPayload,
    OddsProvider,
)
from quiniela.ports.odds_repository import (
    OddsConsensusRecord,
    OddsRepository,
    OddsSnapshotRecord,
)
from quiniela.ports.repositories import MatchRecord, MatchRepository, PredictionRecord, PredictionRepository

__all__ = [
    "Clock",
    "DatabaseInspection",
    "DoctorHealthRepository",
    "FootballDataPayload",
    "FootballDataProvider",
    "MarketProbabilityConverter",
    "MatchRecord",
    "MatchRepository",
    "ModelReleaseInspection",
    "OddsConsensusCounts",
    "OddsConsensusGateway",
    "OddsConsensusRecord",
    "OddsPayload",
    "OddsProvider",
    "OddsRepository",
    "OddsSnapshotRecord",
    "OutboxInspection",
    "PredictionRecord",
    "PredictionRepository",
]
