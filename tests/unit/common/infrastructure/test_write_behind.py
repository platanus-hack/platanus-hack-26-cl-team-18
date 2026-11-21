"""WriteBehindWriter never blocks the caller, retries after a lost connection, counts drops."""

import threading

from app.modules.common.infrastructure.postgres.write_behind import WriteBehindWriter


class FakeCursor:
    def __init__(self, conn: FakeConn) -> None:
        self.conn = conn

    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def execute(self, sql: str, params: object) -> None:
        if self.conn.fail_next:
            self.conn.fail_next -= 1
            raise ConnectionError("lost")
        self.conn.pending.append((sql, params))


class FakeConn:
    def __init__(self, store: list, fail_next: int = 0) -> None:  # type: ignore[type-arg]
        self.store = store
        self.pending: list = []  # type: ignore[type-arg]
        self.fail_next = fail_next
        self.closed = False

    def __enter__(self) -> FakeConn:
        self.pending = []
        return self

    def __exit__(self, exc_type: object, *_: object) -> None:
        if exc_type is None:
            self.store.extend(self.pending)  # commit

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)

    def close(self) -> None:
        self.closed = True


def test_units_commit_in_order() -> None:
    store: list = []  # type: ignore[type-arg]
    writer = WriteBehindWriter(lambda: FakeConn(store), name="t")
    writer.submit((("a", (1,)), ("b", (2,))))
    writer.submit((("c", (3,)),))
    assert writer.flush()
    writer.close()
    assert [sql for sql, _ in store] == ["a", "b", "c"]
    assert writer.stats.written == 3


def test_a_lost_connection_is_reopened_and_the_batch_retried_whole() -> None:
    store: list = []  # type: ignore[type-arg]
    connections: list[FakeConn] = []

    def connect() -> FakeConn:
        conn = FakeConn(store, fail_next=1 if not connections else 0)
        connections.append(conn)
        return conn

    writer = WriteBehindWriter(connect, name="t")
    writer.submit((("a", ()), ("b", ())))
    assert writer.flush(timeout=5)
    writer.close()
    assert [sql for sql, _ in store] == ["a", "b"]  # no half-written unit
    assert writer.stats.failures == 1 and connections[0].closed


def test_submit_never_blocks_and_counts_drops_when_full() -> None:
    gate = threading.Event()

    def slow_connect() -> FakeConn:
        gate.wait(5)
        return FakeConn([])

    writer = WriteBehindWriter(slow_connect, name="t", capacity=1)
    for _ in range(5):
        writer.submit((("x", ()),))
    assert writer.stats.dropped >= 3
    gate.set()
    writer.close()
