"""`make up`: the setup decisions it makes before handing over to the station."""

from __future__ import annotations

import os
from pathlib import Path

from scripts.up import database_target, ensure_demo_key, env_from_example, env_value, needs_npm_ci


def test_a_fresh_env_keeps_records_in_memory() -> None:
    example = "APP_ENV=local\nDATABASE_URL=postgresql://u:p@localhost:5432/db\nDEMO_KEY=\n"
    env = env_from_example(example)
    assert env_value(env, "DATABASE_URL") == ""
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
