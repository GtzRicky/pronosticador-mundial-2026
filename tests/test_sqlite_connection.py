from __future__ import annotations

from pathlib import Path
import sqlite3

from quiniela.adapters.outbound.sqlite.connection import connect_sqlite
from quiniela.adapters.outbound.sqlite.migrations import apply_schema
from quiniela.db import get_connection


def test_get_connection_keeps_legacy_schema_and_row_factory(tmp_path: Path) -> None:
    connection = get_connection(tmp_path / "nested" / "quiniela.sqlite")

    row = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'matches'").fetchone()
    assert row["name"] == "matches"
    assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 30000


def test_connect_sqlite_accepts_schema_initializer(tmp_path: Path) -> None:
    def initialize(connection: sqlite3.Connection) -> None:
        connection.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")

    connection = connect_sqlite(tmp_path / "adapter.sqlite", schema_initializer=initialize)

    with connection:
        connection.execute("INSERT INTO sample (value) VALUES ('ok')")
    row = connection.execute("SELECT value FROM sample").fetchone()
    assert row["value"] == "ok"


def test_apply_schema_runs_migration_after_schema(tmp_path: Path) -> None:
    connection = connect_sqlite(tmp_path / "migration.sqlite")
    migrated: list[str] = []

    def migrate(current: sqlite3.Connection) -> None:
        current.execute("CREATE INDEX idx_sample_value ON sample(value)")
        migrated.append("done")

    apply_schema(
        connection,
        ["CREATE TABLE sample (id INTEGER PRIMARY KEY, value TEXT NOT NULL)"],
        migrate=migrate,
    )

    index_row = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'index' AND name = 'idx_sample_value'"
    ).fetchone()
    assert index_row["name"] == "idx_sample_value"
    assert migrated == ["done"]
