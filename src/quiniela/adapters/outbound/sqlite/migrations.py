from __future__ import annotations

from collections.abc import Callable, Iterable
import sqlite3


SchemaMigration = Callable[[sqlite3.Connection], None]


def apply_schema(
    connection: sqlite3.Connection,
    schema_statements: Iterable[str],
    *,
    migrate: SchemaMigration | None = None,
) -> None:
    with connection:
        for statement in schema_statements:
            connection.execute(statement)
    if migrate is not None:
        migrate(connection)
