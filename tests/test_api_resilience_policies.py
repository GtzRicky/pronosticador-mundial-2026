from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import requests

from quiniela.adapters.outbound.api_football.policies import CircuitBreakerPolicy, EndpointBudgetPolicy, RateLimitPolicy
from quiniela.api_football_client import APIFootballClient, APILimitReachedError
from quiniela.config import get_settings
from quiniela.db import count_api_requests_today, record_api_usage, set_cached_response


class FakeResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"status={self.status_code}")

    def json(self) -> dict:
        return self._payload


class FakeSession:
    def __init__(self, *results):
        self.results = list(results)
        self.calls = 0

    def get(self, url, **kwargs):
        self.calls += 1
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def _settings(tmp_path: Path, *, daily_limit: int = 100):
    return replace(
        get_settings(),
        db_path=tmp_path / "api-policy.sqlite",
        api_football_key="test-key",
        api_daily_limit=daily_limit,
        api_critical_reserve=25,
    )


def test_rate_limit_policy_blocks_daily_limit() -> None:
    policy = RateLimitPolicy(daily_limit=2, critical_reserve=1)

    assert policy.requests_remaining(1) == 1
    assert policy.can_make_request(1).allowed is True
    blocked = policy.can_make_request(2)
    assert blocked.allowed is False
    assert blocked.reason == "quota_exceeded"


def test_endpoint_budget_policy_protects_critical_reserve() -> None:
    policy = EndpointBudgetPolicy(critical_reserve=25)

    blocked = policy.can_call_endpoint("pre_match", "/fixtures/statistics", 30)
    protected = policy.can_call_endpoint("pre_match", "/odds", 10)
    allowed = policy.can_call_endpoint("pre_match", "/fixtures/lineups", 10)

    assert blocked.reason == "endpoint_not_allowed_for_mode"
    assert protected.reason == "critical_reserve_protected"
    assert allowed.allowed is True


def test_circuit_breaker_opens_and_half_opens_after_reset() -> None:
    now = datetime(2026, 6, 22, tzinfo=timezone.utc)
    breaker = CircuitBreakerPolicy(failure_threshold=2, reset_after_seconds=60)

    breaker.record_failure(now)
    assert breaker.before_request(now).allowed is True
    breaker.record_failure(now)
    assert breaker.before_request(now).reason == "circuit_open"
    assert breaker.before_request(now + timedelta(seconds=61)).reason == "half_open"
    breaker.record_success()
    assert breaker.before_request(now).allowed is True


def test_cache_hit_does_not_consume_live_quota(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    client = APIFootballClient(settings=settings, session=FakeSession())
    set_cached_response(
        client.connection,
        "/fixtures",
        {"date": "2026-06-18"},
        {"response": [{"fixture": {"id": 123}}], "results": 1},
        status_code=200,
    )

    payload = client.get_fixtures(date="2026-06-18")

    assert payload["results"] == 1
    assert count_api_requests_today(client.connection) == 0


def test_daily_limit_blocks_live_request(tmp_path: Path) -> None:
    settings = _settings(tmp_path, daily_limit=1)
    client = APIFootballClient(settings=settings, force_refresh=True, session=FakeSession())
    record_api_usage(client.connection, "/fixtures", {"date": "2026-06-18"}, cache_hit=False, status_code=200)

    with pytest.raises(APILimitReachedError, match="limite diario"):
        client.get_fixtures(date="2026-06-18")


def test_429_blocks_with_expected_error(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    session = FakeSession(FakeResponse(429, {"response": [], "results": 0}))
    client = APIFootballClient(settings=settings, force_refresh=True, session=session)

    with pytest.raises(APILimitReachedError, match="429"):
        client.get_fixtures(date="2026-06-18")


def test_transient_status_retries_then_succeeds(tmp_path: Path, monkeypatch) -> None:
    settings = _settings(tmp_path)
    session = FakeSession(
        FakeResponse(500, {"response": [], "results": 0}),
        FakeResponse(200, {"response": [{"fixture": {"id": 123}}], "results": 1}),
    )
    client = APIFootballClient(settings=settings, force_refresh=True, session=session)
    monkeypatch.setattr("quiniela.api_football_client.time.sleep", lambda _: None)

    payload = client.get_fixtures(date="2026-06-18")

    assert session.calls == 2
    assert payload["results"] == 1
