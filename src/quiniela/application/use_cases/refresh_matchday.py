from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol


class MatchdayRunnerFacade(Protocol):
    def run(self, now: datetime | None = None) -> dict[str, Any]:
        """Run one matchday automation cycle."""


@dataclass(frozen=True)
class RefreshMatchdayCommand:
    now: datetime | None = None


@dataclass(frozen=True)
class RefreshMatchdayResult:
    summary: dict[str, Any]


class RefreshMatchdayUseCase:
    def __init__(self, runner: MatchdayRunnerFacade | None = None) -> None:
        self._runner = runner

    @property
    def runner(self) -> MatchdayRunnerFacade:
        if self._runner is None:
            from quiniela.matchday import MatchdayRunner

            self._runner = MatchdayRunner()
        return self._runner

    def execute(self, command: RefreshMatchdayCommand) -> RefreshMatchdayResult:
        return RefreshMatchdayResult(summary=self.runner.run(command.now))
