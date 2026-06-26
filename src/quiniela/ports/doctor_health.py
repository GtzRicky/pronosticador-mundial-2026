from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class DatabaseInspection:
    exists: bool
    tables: frozenset[str] = frozenset()
    scope_initialized: bool = False
    error: str | None = None


@dataclass(frozen=True)
class OutboxInspection:
    available: bool
    counts: dict[str, int] = field(default_factory=dict)
    error: str | None = None


@dataclass(frozen=True)
class ModelReleaseInspection:
    available: bool
    release_id: str | None = None
    model_version: str | None = None
    preliminary: bool = False
    error: str | None = None


class DoctorHealthRepository(Protocol):
    def inspect_database(
        self,
        *,
        competition_id: str,
        season_id: str,
    ) -> DatabaseInspection:
        """Inspect schema and competition scope without mutating the database."""

    def inspect_outbox(
        self,
        *,
        competition_id: str,
        season_id: str,
    ) -> OutboxInspection:
        """Return open notification counts for one competition season."""

    def inspect_active_model_release(
        self,
        *,
        competition_id: str,
        season_id: str,
    ) -> ModelReleaseInspection:
        """Return the active model release for one competition season."""
