from __future__ import annotations

from contextlib import closing
from pathlib import Path
import sqlite3

from quiniela.ports.doctor_health import (
    DatabaseInspection,
    ModelReleaseInspection,
    OutboxInspection,
)


class SQLiteDoctorHealthRepository:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path

    def inspect_database(
        self,
        *,
        competition_id: str,
        season_id: str,
    ) -> DatabaseInspection:
        if not self._db_path.exists():
            return DatabaseInspection(exists=False)
        try:
            with closing(self._connect_readonly()) as connection:
                rows = connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
                tables = frozenset(str(row["name"]) for row in rows)
                if "seasons" not in tables:
                    return DatabaseInspection(exists=True, tables=tables)
                scope = connection.execute(
                    """
                    SELECT COUNT(*) AS total
                    FROM seasons
                    WHERE competition_id = ? AND season_id = ?
                    """,
                    (competition_id, season_id),
                ).fetchone()
        except sqlite3.OperationalError as exc:
            return DatabaseInspection(exists=True, error=str(exc))
        return DatabaseInspection(
            exists=True,
            tables=tables,
            scope_initialized=scope is not None and int(scope["total"]) > 0,
        )

    def inspect_outbox(
        self,
        *,
        competition_id: str,
        season_id: str,
    ) -> OutboxInspection:
        if not self._db_path.exists():
            return OutboxInspection(available=False)
        try:
            with closing(self._connect_readonly()) as connection:
                rows = connection.execute(
                    """
                    SELECT status, COUNT(*) AS total
                    FROM notification_deliveries
                    WHERE status IN ('pending', 'waiting_prediction', 'retry', 'sending')
                      AND competition_id = ?
                      AND season_id = ?
                    GROUP BY status
                    """,
                    (competition_id, season_id),
                ).fetchall()
        except sqlite3.OperationalError as exc:
            return OutboxInspection(available=True, error=str(exc))
        return OutboxInspection(
            available=True,
            counts={str(row["status"]): int(row["total"]) for row in rows},
        )

    def inspect_active_model_release(
        self,
        *,
        competition_id: str,
        season_id: str,
    ) -> ModelReleaseInspection:
        if not self._db_path.exists():
            return ModelReleaseInspection(available=False)
        try:
            with closing(self._connect_readonly()) as connection:
                row = connection.execute(
                    """
                    SELECT release_id, model_version, preliminary
                    FROM model_releases
                    WHERE status = 'active'
                      AND competition_id = ?
                      AND season_id = ?
                    ORDER BY activated_at DESC, id DESC
                    LIMIT 1
                    """,
                    (competition_id, season_id),
                ).fetchone()
        except sqlite3.OperationalError as exc:
            return ModelReleaseInspection(available=True, error=str(exc))
        if row is None:
            return ModelReleaseInspection(available=True)
        return ModelReleaseInspection(
            available=True,
            release_id=str(row["release_id"]),
            model_version=str(row["model_version"]),
            preliminary=bool(row["preliminary"]),
        )

    def _connect_readonly(self) -> sqlite3.Connection:
        uri = self._db_path.resolve().as_uri() + "?mode=ro"
        connection = sqlite3.connect(uri, uri=True)
        connection.row_factory = sqlite3.Row
        return connection
