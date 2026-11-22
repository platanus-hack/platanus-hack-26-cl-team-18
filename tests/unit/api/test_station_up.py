"""`make up`: the setup decisions it makes before handing over to the station."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from scripts.up import (
    database_target,
    ensure_database_url,
    ensure_demo_key,
    env_from_example,
    env_value,
    needs_npm_ci,
)


def test_a_fresh_env_keeps_persistent_records() -> None:
    example = "APP_ENV=local\nDATABASE_URL=postgresql://u:p@localhost:5432/db\nDEMO_KEY=\n"
    env = env_from_example(example)
    assert env_value(env, "DATABASE_URL") == "postgresql://u:p@localhost:5432/db"
    assert env_value(env, "APP_ENV") == "local"


def test_an_empty_demo_key_is_filled_and_a_set_one_is_never_replaced() -> None:
    filled, added = ensure_demo_key("A=1\nDEMO_KEY=\nB=2\n", "tok")
    assert added and filled == "A=1\nDEMO_KEY=tok\nB=2\n"
    kept, added = ensure_demo_key('DEMO_KEY="mine"\n', "tok")
    assert not added and kept == 'DEMO_KEY="mine"\n'


def test_a_missing_demo_key_is_appended() -> None:
    assert ensure_demo_key("A=1", "tok") == ("A=1\nDEMO_KEY=tok\n", True)
    assert ensure_demo_key("", "tok") == ("DEMO_KEY=tok\n", True)


def test_database_target_only_when_configured() -> None:
    assert database_target("") is None
    assert database_target("postgresql://u:p@db.internal:6543/x") == ("db.internal", 6543)
    assert database_target("postgresql://u:p@localhost/x") == ("localhost", 5432)


def test_npm_ci_runs_only_when_the_lock_changed(tmp_path: Path) -> None:
    lock = tmp_path / "package-lock.json"
    lock.write_text("{}")
    assert needs_npm_ci(tmp_path)  # never installed
    installed = tmp_path / "node_modules" / ".package-lock.json"
    installed.parent.mkdir()
    installed.write_text("{}")
    os.utime(lock, (1, 1))
    assert not needs_npm_ci(tmp_path)
    os.utime(lock, None)
    os.utime(installed, (1, 1))
    assert needs_npm_ci(tmp_path)


def test_a_database_that_is_down_stops_a_new_station_instead_of_losing_money(
    monkeypatch, capsys
) -> None:
    import scripts.up as up

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(up, "_reachable", lambda host, port: False)
    with pytest.raises(SystemExit, match="persistent records"):
        up.prepare_database("DATABASE_URL=postgresql://u:p@db.example.org:5432/x\n")

    def failing_run(command, **_kwargs):
        raise SystemExit(f"failed: {' '.join(command)}")

    monkeypatch.setattr(up, "_run", failing_run)
    monkeypatch.setattr(up.shutil, "which", lambda tool: "/usr/bin/docker")
    with pytest.raises(SystemExit, match="persistent records"):
        up.prepare_database("DATABASE_URL=postgresql://u:p@localhost:5432/x\n")


def test_old_memory_only_install_is_upgraded_without_overwriting_an_existing_database():
    assert env_value(ensure_database_url("DATABASE_URL=\n"), "DATABASE_URL").startswith(
        "postgresql://"
    )
    configured = "DATABASE_URL=postgresql://u:p@db.example.org/db\n"
    assert ensure_database_url(configured) == configured
