from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from quiniela.domain.value_objects import MatchId, PredictionId, TeamId


@dataclass(frozen=True)
class MatchRecord:
    match_id: MatchId
    home_team_id: TeamId
    away_team_id: TeamId
    kickoff_at: datetime
    status: str
    date_cdmx: str | None = None
    home_team: str | None = None
    away_team: str | None = None
    group_name: str | None = None
    stage: str | None = None
    api_fixture_id: int | None = None


@dataclass(frozen=True)
class PredictionRecord:
    prediction_id: PredictionId
    match_id: MatchId
    predicted_score: str
    model_version: str
    generated_at: datetime


class MatchRepository(Protocol):
    def get_by_id(self, match_id: MatchId) -> MatchRecord | None:
        """Return a match by domain id when it exists."""

    def list_by_date(self, date_cdmx: str) -> list[MatchRecord]:
        """Return matches for an operational CDMX date."""


class PredictionRepository(Protocol):
    def get_latest_for_match(self, match_id: MatchId) -> PredictionRecord | None:
        """Return the latest prediction for a match when it exists."""

    def save(self, prediction: PredictionRecord) -> PredictionId:
        """Persist a prediction and return its id."""
