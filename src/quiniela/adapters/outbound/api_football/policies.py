from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    reason: str | None = None


@dataclass(frozen=True)
class RateLimitPolicy:
    daily_limit: int
    critical_reserve: int = 0

    def requests_remaining(self, used_requests: int) -> int:
        return max(int(self.daily_limit) - int(used_requests), 0)

    def can_make_request(self, used_requests: int) -> PolicyDecision:
        if int(used_requests) >= int(self.daily_limit):
            return PolicyDecision(False, "quota_exceeded")
        return PolicyDecision(True)


@dataclass(frozen=True)
class EndpointBudgetPolicy:
    critical_reserve: int

    CRITICAL_ENDPOINTS = frozenset(
        {
            "/fixtures",
            "/fixtures/lineups",
        }
    )
    MODE_ENDPOINTS = {
        "full": frozenset(
            {
                "/fixtures",
                "/fixtures/lineups",
                "/fixtures/statistics",
                "/fixtures/events",
                "/fixtures/players",
                "/odds",
                "/players",
                "/players/seasons",
            }
        ),
        "hourly": frozenset({"/fixtures", "/fixtures/lineups", "/odds"}),
        "pre_match": frozenset({"/fixtures", "/fixtures/lineups", "/odds"}),
        "post_status": frozenset({"/fixtures"}),
        "lineups": frozenset({"/fixtures", "/fixtures/lineups"}),
    }

    def can_call_endpoint(self, mode: str, endpoint: str, requests_remaining: int) -> PolicyDecision:
        allowed_endpoints = self.MODE_ENDPOINTS.get(mode)
        if allowed_endpoints is not None and endpoint not in allowed_endpoints:
            return PolicyDecision(False, "endpoint_not_allowed_for_mode")
        if requests_remaining <= self.critical_reserve and endpoint not in self.CRITICAL_ENDPOINTS:
            return PolicyDecision(False, "critical_reserve_protected")
        return PolicyDecision(True)


@dataclass
class CircuitBreakerPolicy:
    failure_threshold: int = 3
    reset_after_seconds: int = 300
    failure_count: int = 0
    opened_at: datetime | None = None

    def before_request(self, now: datetime | None = None) -> PolicyDecision:
        if self.opened_at is None:
            return PolicyDecision(True)
        current = now or datetime.now(timezone.utc)
        if current - self.opened_at >= timedelta(seconds=self.reset_after_seconds):
            return PolicyDecision(True, "half_open")
        return PolicyDecision(False, "circuit_open")

    def record_success(self) -> None:
        self.failure_count = 0
        self.opened_at = None

    def record_failure(self, now: datetime | None = None) -> None:
        self.failure_count += 1
        if self.failure_count >= self.failure_threshold:
            self.opened_at = now or datetime.now(timezone.utc)
