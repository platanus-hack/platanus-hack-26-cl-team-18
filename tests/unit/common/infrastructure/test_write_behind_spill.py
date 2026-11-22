"""Durable units survive a dead database: spilled to JSONL, replayed once at the next start."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from app.modules.common.infrastructure.postgres.spill import (
    SpillFile,
    decode_unit,
    encode_unit,
    replay_spill,
)
from app.modules.common.infrastructure.postgres.write_behind import WriteBehindWriter

from .test_write_behind import FakeConn

AT = datetime(2026, 11, 21, 15, 0, 1, 250_000, tzinfo=UTC)
CHARGE = (("INSERT charge", ("ch-1", Decimal("7.40"), Decimal("114.00"), AT, None, True)),)
AUCTION = (("INSERT auction", ("au-1",)),)


def dead() -> FakeConn:
    raise ConnectionError("database down")


def test_params_keep_their_types_through_the_file() -> None:
    unit = (("x", (Decimal("0.10"), AT, UUID(int=7), "s", 3, None, [Decimal("1")])),)
    assert decode_unit(encode_unit(unit)) == (
        ("x", (Decimal("0.10"), AT, UUID(int=7), "s", 3, None, [Decimal("1")])),
    )


def test_no_spill_file_until_something_spills(tmp_path: Path) -> None:
    store: list = []  # type: ignore[type-arg]
    path = tmp_path / "var" / "spill.jsonl"
    writer = WriteBehindWriter(lambda: FakeConn(store), name="t", spill=SpillFile(path))
    writer.submit(CHARGE)
    writer.close()
    assert store and not path.exists()


def test_at_shutdown_with_the_database_down_durable_units_spill_and_others_drop(
    tmp_path: Path,
) -> None:
    spill = SpillFile(tmp_path / "spill.jsonl")
    writer = WriteBehindWriter(
        dead,
        name="t",
        spill=spill,
        durable=lambda unit: all(sql == "INSERT charge" for sql, _ in unit),
    )
    writer.submit(CHARGE)
    writer.submit(AUCTION)
    writer.close(timeout=0.3)
    # The stuck writer thread may spill its batch too: a duplicate replays once.
    assert {encode_unit(unit) for unit in spill.read()} == {encode_unit(CHARGE)}
    assert writer.stats.spilled >= 1 and writer.stats.dropped >= 1


def test_past_a_full_overflow_durable_units_spill_instead_of_dropping(tmp_path: Path) -> None:
    import threading

    gate = threading.Event()

    def slow() -> FakeConn:
        gate.wait(5)
        return FakeConn([])

    spill = SpillFile(tmp_path / "spill.jsonl")
    writer = WriteBehindWriter(slow, name="t", capacity=1, overflow_capacity=1, spill=spill)
    for _ in range(6):
        writer.submit(CHARGE)
    assert writer.stats.spilled >= 1
    assert writer.stats.dropped == 0
    gate.set()
    writer.close()


def test_replay_writes_every_unit_once_and_removes_the_file(tmp_path: Path) -> None:
    spill = SpillFile(tmp_path / "spill.jsonl")
    spill.append([CHARGE, CHARGE])  # the same charge twice: ON CONFLICT keeps one
    store: list = []  # type: ignore[type-arg]
    assert replay_spill(spill, lambda: FakeConn(store)) == 2
    assert [params for _, params in store] == [CHARGE[0][1], CHARGE[0][1]]
    assert not spill.path.exists()
    assert replay_spill(spill, lambda: FakeConn(store)) == 0  # nothing left to replay


def test_replay_with_the_database_still_down_keeps_the_file(tmp_path: Path) -> None:
    spill = SpillFile(tmp_path / "spill.jsonl")
    spill.append([CHARGE])
    assert replay_spill(spill, dead) == 0
    assert spill.read() == [CHARGE]


def test_replay_interrupted_midway_keeps_only_what_was_not_written(tmp_path: Path) -> None:
    spill = SpillFile(tmp_path / "spill.jsonl")
    second = (("INSERT charge", ("ch-2",)),)
    spill.append([CHARGE, second])
    store: list = []  # type: ignore[type-arg]
    conn = FakeConn(store)
    calls = {"n": 0}
    original = conn.cursor

    def cursor() -> object:
        calls["n"] += 1
        if calls["n"] == 2:
            conn.fail_next = 1  # the connection drops before the second unit
        return original()

    conn.cursor = cursor  # type: ignore[method-assign]
    assert replay_spill(spill, lambda: conn) == 1
    assert spill.read() == [second]


def test_a_torn_last_line_is_skipped(tmp_path: Path) -> None:
    spill = SpillFile(tmp_path / "spill.jsonl")
    spill.append([CHARGE])
    with spill.path.open("a", encoding="utf-8") as handle:
        handle.write('{"statements": [["INSERT')  # crash mid-write
    assert spill.read() == [CHARGE]
