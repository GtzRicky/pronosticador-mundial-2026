from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from quiniela.application.use_cases.refresh_matchday import RefreshMatchdayCommand, RefreshMatchdayUseCase


class FakeRunner:
    def __init__(self) -> None:
        self.calls: list[datetime | None] = []

    def run(self, now: datetime | None = None) -> dict[str, Any]:
        self.calls.append(now)
        return {"now": now.isoformat() if now else None, "completed": ["daily"]}


def test_refresh_matchday_use_case_delegates_to_runner() -> None:
    runner = FakeRunner()
    use_case = RefreshMatchdayUseCase(runner=runner)
    now = datetime(2026, 6, 13, 10, 1, tzinfo=timezone.utc)

    result = use_case.execute(RefreshMatchdayCommand(now=now))

    assert runner.calls == [now]
    assert result.summary == {"now": "2026-06-13T10:01:00+00:00", "completed": ["daily"]}


def test_refresh_matchday_use_case_accepts_default_now() -> None:
    runner = FakeRunner()
    use_case = RefreshMatchdayUseCase(runner=runner)

    result = use_case.execute(RefreshMatchdayCommand())

    assert runner.calls == [None]
    assert result.summary == {"now": None, "completed": ["daily"]}
