"""Apply db/migrations/*.sql in order, exactly once.

    uv run python scripts/migrate.py            # apply pending migrations
    uv run python scripts/migrate.py status     # list applied / pending

Reads DATABASE_URL. On Render it runs as the web service's pre_deploy_command,
so a failed migration aborts the deploy and the old version keeps serving.

Guarantees:
- one migrator at a time (Postgres advisory lock), so overlapping deploys serialise
- each file runs in its own transaction together with its ledger row
- an applied file whose content changed is an error, never silently skipped
  or re-run. Fix forward with a new numbered file.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
MIGRATIONS_DIR = ROOT_DIR / "db" / "migrations"
MIGRATION_LOCK_ID = 18_260_001  # arbitrary, constant per project
_FILENAME = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")

LEDGER_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version text PRIMARY KEY,
    filename text NOT NULL,
    checksum text NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT now()
)
"""


@dataclass(frozen=True, slots=True)
class Migration:
    version: str
    filename: str
    checksum: str
    sql: str


class MigrationError(RuntimeError):
    pass


def discover(directory: Path = MIGRATIONS_DIR) -> list[Migration]:
    migrations: list[Migration] = []
    seen: dict[str, str] = {}
    for path in sorted(directory.glob("*.sql")):
        match = _FILENAME.match(path.name)
        if not match:
            raise MigrationError(f"{path.name}: expected NNNN_snake_case.sql")
        version = match.group(1)
        if version in seen:
            raise MigrationError(f"duplicate version {version}: {seen[version]} and {path.name}")
        seen[version] = path.name
        sql = path.read_text(encoding="utf-8")
        checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()
        migrations.append(Migration(version, path.name, checksum, sql))
    return migrations


def pending(migrations: list[Migration], applied: dict[str, str]) -> list[Migration]:
    """Migrations still to run, given applied {version: checksum}. Raises on drift."""
    known = {m.version for m in migrations}
    missing = sorted(set(applied) - known)
    if missing:
        raise MigrationError(f"applied migrations missing from db/migrations: {missing}")
    for m in migrations:
        if m.version in applied and applied[m.version] != m.checksum:
            raise MigrationError(
                f"{m.filename} changed after it was applied. "
                "Restore the original and add a new migration instead."
            )
    todo = [m for m in migrations if m.version not in applied]
    if todo and applied and todo[0].version < max(applied):
        raise MigrationError(
            f"{todo[0].filename} is older than the latest applied migration {max(applied)}. "
            "Renumber it after the latest one."
        )
    return todo


def _connect():  # pragma: no cover - needs a database
    import psycopg2

    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("ERROR: DATABASE_URL is not set.")
    return psycopg2.connect(url)


def _applied(cur) -> dict[str, str]:  # pragma: no cover - needs a database
    cur.execute("SELECT version, checksum FROM schema_migrations")
    return dict(cur.fetchall())


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - needs a database
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", nargs="?", default="migrate", choices=("migrate", "status"))
    args = parser.parse_args(argv)

    migrations = discover()
    conn = _connect()
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(LEDGER_DDL)
            cur.execute("SELECT pg_advisory_lock(%s)", (MIGRATION_LOCK_ID,))
            applied = _applied(cur)
        todo = pending(migrations, applied)

        if args.command == "status":
            for m in migrations:
                print(f"{'applied' if m.version in applied else 'PENDING'}  {m.filename}")
            return 0

        if not todo:
            print(f"==> schema up to date ({len(applied)} applied)")
            return 0
        conn.autocommit = False
        for m in todo:
            print(f"==> applying {m.filename}")
            with conn, conn.cursor() as cur:  # commits on success, rolls back on error
                cur.execute(m.sql)
                cur.execute(
                    "INSERT INTO schema_migrations (version, filename, checksum) VALUES (%s, %s, %s)",
                    (m.version, m.filename, m.checksum),
                )
        print(f"==> applied {len(todo)} migration(s)")
        return 0
    except MigrationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()  # also releases the session advisory lock


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
