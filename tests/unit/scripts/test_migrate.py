"""scripts/migrate.py ordering and drift rules (no database)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "migrate", Path(__file__).resolve().parents[3] / "scripts" / "migrate.py"
)
assert _SPEC and _SPEC.loader
migrate = importlib.util.module_from_spec(_SPEC)
sys.modules["migrate"] = migrate  # dataclasses resolve their module by name
_SPEC.loader.exec_module(migrate)


def _write(tmp_path: Path, name: str, sql: str = "SELECT 1;") -> None:
    (tmp_path / name).write_text(sql, encoding="utf-8")


def test_discovers_in_version_order(tmp_path: Path) -> None:
    _write(tmp_path, "0002_b.sql")
    _write(tmp_path, "0001_a.sql")
    assert [m.version for m in migrate.discover(tmp_path)] == ["0001", "0002"]


def test_rejects_bad_filename_and_duplicate_version(tmp_path: Path) -> None:
    _write(tmp_path, "init.sql")
    with pytest.raises(migrate.MigrationError, match="NNNN_snake_case"):
        migrate.discover(tmp_path)

    (tmp_path / "init.sql").unlink()
    _write(tmp_path, "0001_a.sql")
    _write(tmp_path, "0001_b.sql")
    with pytest.raises(migrate.MigrationError, match="duplicate version 0001"):
        migrate.discover(tmp_path)


def test_pending_skips_applied(tmp_path: Path) -> None:
    _write(tmp_path, "0001_a.sql")
    _write(tmp_path, "0002_b.sql")
    found = migrate.discover(tmp_path)
    todo = migrate.pending(found, {"0001": found[0].checksum})
    assert [m.version for m in todo] == ["0002"]


def test_edited_applied_migration_is_an_error(tmp_path: Path) -> None:
    _write(tmp_path, "0001_a.sql", "SELECT 2;")
    found = migrate.discover(tmp_path)
    with pytest.raises(migrate.MigrationError, match="changed after it was applied"):
        migrate.pending(found, {"0001": "checksum-of-the-original"})


def test_applied_migration_deleted_from_repo_is_an_error(tmp_path: Path) -> None:
    _write(tmp_path, "0002_b.sql")
    found = migrate.discover(tmp_path)
    with pytest.raises(migrate.MigrationError, match="missing from db/migrations"):
        migrate.pending(found, {"0001": "x", "0002": found[0].checksum})


def test_out_of_order_new_migration_is_an_error(tmp_path: Path) -> None:
    _write(tmp_path, "0001_a.sql")
    _write(tmp_path, "0002_b.sql")
    found = migrate.discover(tmp_path)
    with pytest.raises(migrate.MigrationError, match="older than the latest applied"):
        migrate.pending(found, {"0002": found[1].checksum})


def test_repo_migrations_are_well_formed() -> None:
    assert migrate.discover(), "db/migrations should not be empty"
