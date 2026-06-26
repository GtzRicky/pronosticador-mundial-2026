from __future__ import annotations

from pathlib import Path
import sqlite3
from typing import Callable


SchemaInitializer = Callable[[sqlite3.Connection], None]


def configure_connection(connection: sqlite3.Connection) -> sqlite3.Connection:
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 30000")
    return connection


def connect_sqlite(
    db_path: Path,
    *,
    schema_initializer: SchemaInitializer | None = None,
    timeout_seconds: int = 30,
) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path, timeout=timeout_seconds)
    configure_connection(connection)
    if schema_initializer is not None:
        schema_initializer(connection)
    return connection
