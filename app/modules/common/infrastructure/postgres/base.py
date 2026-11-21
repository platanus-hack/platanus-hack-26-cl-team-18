"""Postgres infrastructure interfaces.

Architectural intent:
- Define minimal DB contract for infrastructure usage.
- Keep application/domain unaware of DB specifics.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol

from psycopg2.extensions import connection as PGConnection, cursor as PGCursor
from psycopg2.extras import execute_batch


class PostgresReadExecutor(Protocol):
    """Statement execution without ownership of a pool or transaction."""

    def execute(
        self,
        query: str,
        params: Any = None,
        *,
        fetch: bool = False,
        fetchone: bool = False,
        fetchall: bool = False,
    ) -> Any: ...


class IPostgresDB(PostgresReadExecutor, Protocol):
    """Minimal Postgres pool contract for infrastructure code."""

    def get_connection(self) -> PGConnection: ...
    def release_connection(self, conn: PGConnection) -> None: ...
    def close_all(self) -> None: ...
    def execute(
        self,
        query: str,
        params: Any = None,
        *,
        fetch: bool = False,
        fetchone: bool = False,
        fetchall: bool = False,
    ) -> Any: ...
    def cursor(self) -> Any: ...


def escape_like(value: str) -> str:
    """Escape LIKE/ILIKE metacharacters (%, _, \\) in a search term.

    Use this whenever user-supplied text is wrapped in wildcards for
    a LIKE / ILIKE clause.  The escaped value must be paired with
    ``ESCAPE '\\'`` in the SQL (PostgreSQL default).

    Example::

        pattern = f"%{escape_like(user_input)}%"
        cur.execute("SELECT ... WHERE public.f_unaccent(name) ILIKE public.f_unaccent(%s)", (pattern,))
    """
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def execute_many(cursor: Any, sql: str, rows: Sequence[Any]) -> None:
    """Run one statement for many parameter rows in a few round trips.

    psycopg2's ``cursor.executemany`` sends one round trip per row; on a
    50-row ledger write that is 50 network waits inside the write transaction.
    ``execute_batch`` sends the same statement, unchanged, in pages of 100.
    It does not report a per-row ``rowcount``, so use this only where the
    caller ignores it.

    Cursors that are not psycopg2 cursors (test doubles) have no ``mogrify``,
    so they keep ``executemany`` semantics.
    """
    if isinstance(cursor, PGCursor):
        execute_batch(cursor, sql, rows, page_size=100)
    else:
        cursor.executemany(sql, rows)
