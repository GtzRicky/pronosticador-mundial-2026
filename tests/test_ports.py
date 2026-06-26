from __future__ import annotations

from datetime import datetime, timezone

from quiniela.domain import MatchId, PredictionId, TeamId
from quiniela.infrastructure.clock import SystemClock
from quiniela.ports.clock import Clock
from quiniela.ports.repositories import MatchRecord, PredictionRecord


def test_system_clock_implements_clock_protocol() -> None:
    clock = SystemClock()

    assert isinstance(clock, Clock)
    assert clock.now().tzinfo is not None


def test_match_record_uses_domain_identifiers() -> None:
    kickoff = datetime(2026, 6, 11, 19, 0, tzinfo=timezone.utc)

    record = MatchRecord(
        match_id=MatchId("match_001"),
        home_team_id=TeamId("mexico"),
        away_team_id=TeamId("canada"),
        kickoff_at=kickoff,
        status="scheduled",
    )

    assert str(record.match_id) == "match_001"
    assert record.kickoff_at is kickoff


def test_prediction_record_uses_domain_identifiers() -> None:
    generated_at = datetime(2026, 6, 11, 18, 0, tzinfo=timezone.utc)

    record = PredictionRecord(
        prediction_id=PredictionId("prediction_001"),
        match_id=MatchId("match_001"),
        predicted_score="1-0",
        model_version="poisson_v1",
        generated_at=generated_at,
    )

    assert record.predicted_score == "1-0"
    assert record.generated_at is generated_at
