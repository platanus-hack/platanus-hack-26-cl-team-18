from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import anyio
import pytest

from app.contract.messages import (
    ConsoleDegradation,
    DegradationFlag,
    ErrorMessage,
)
from app.realtime.hub import ConnectionHub
from app.realtime.mailbox import ConflatingMailbox
from app.realtime.router import InboundRouter, Received
from app.realtime.seq_gate import SeqGate


def test_seq_gate_drops_duplicates_and_out_of_order_per_stream() -> None:
    gate = SeqGate()
    assert gate.admit("a", 1)
    assert not gate.admit("a", 1)
    assert not gate.admit("a", 0)
    assert gate.admit("a", 5)
    assert gate.admit("b", 0)  # streams are independent
    assert gate.dropped == 2


def test_mailbox_latest_wins_per_key_and_keeps_fifo_across_keys() -> None:
    async def run() -> list[str]:
        box: ConflatingMailbox[str] = ConflatingMailbox(max_items=8)
        box.put("snapshot", "s1")
        box.put("utt:1", "u1")
        box.put("snapshot", "s2")  # supersedes s1, moves to the back
        return [await box.get(), await box.get()]

    assert anyio.run(run) == ["u1", "s2"]


def test_mailbox_overflow_drops_the_oldest() -> None:
    async def run() -> tuple[list[int], int]:
        box: ConflatingMailbox[int] = ConflatingMailbox(max_items=2)
        for i in range(3):
            box.put(i, i)
        return [await box.get(), await box.get()], box.stats.overflowed

    assert anyio.run(run) == ([1, 2], 1)


def test_mailbox_get_waits_for_a_put() -> None:
    async def run() -> str:
        box: ConflatingMailbox[str] = ConflatingMailbox()

        async def later() -> None:
            await anyio.sleep(0.01)
            box.put("k", "v")

        async with anyio.create_task_group() as tg:
            tg.start_soon(later)
            with anyio.fail_after(1):
                return await box.get()

    assert anyio.run(run) == "v"


class _Sink:
    def __init__(self, *, fail: bool = False) -> None:
        self.sent: list[str] = []
        self.fail = fail

    async def send_text(self, data: str) -> None:
        if self.fail:
            raise RuntimeError("socket closed")
        self.sent.append(data)


def test_hub_targets_one_screen_and_drops_dead_sinks() -> None:
    async def run() -> tuple[_Sink, _Sink, _Sink, ConnectionHub]:
        hub = ConnectionHub()
        mine, other, dead = _Sink(), _Sink(), _Sink(fail=True)
        hub.add_player("s1", mine)
        hub.add_player("s1", dead)
        hub.add_player("s2", other)
        await hub.send_to_screen(ErrorMessage(code="x", message="y"), screen_id="s1")
        return mine, other, dead, hub

    mine, other, _, hub = anyio.run(run)
    assert len(mine.sent) == 1 and other.sent == []
    assert hub.stats.dropped_sinks == 1
    assert hub.connected_screens() == ["s1", "s2"]


def test_hub_threadsafe_publish_reaches_consoles() -> None:
    async def run() -> _Sink:
        hub = ConnectionHub()
        hub.bind_loop(asyncio.get_running_loop())
        console = _Sink()
        hub.add_console(console)
        msg = ConsoleDegradation(screen_id="s1", flags=[DegradationFlag.VLM_UNAVAILABLE])
        await anyio.to_thread.run_sync(hub.broadcast_console_threadsafe, msg)
        with anyio.fail_after(1):
            while not console.sent:
                await anyio.sleep(0.001)
        return console

    assert '"vlm_unavailable"' in anyio.run(run).sent[0]


def test_hub_without_a_loop_drops_instead_of_raising() -> None:
    hub = ConnectionHub()
    hub.broadcast_console_threadsafe(ErrorMessage(code="x", message="y"))
    assert hub.stats.dropped_no_loop == 1


def test_router_isolates_a_failing_handler() -> None:
    from app.contract.messages import PlaybackReport

    seen: list[str] = []
    router = InboundRouter()

    def boom(msg: PlaybackReport, received: Received) -> None:
        raise RuntimeError("handler bug")

    router.on(PlaybackReport, boom)
    router.on(PlaybackReport, lambda msg, received: seen.append(msg.creative_id))
    t0 = datetime(2026, 11, 21, tzinfo=UTC)
    report = PlaybackReport(screen_id="s", creative_id="c1", seq=1, started_at=t0, ended_at=t0)
    anyio.run(router.dispatch, report, Received(received_at=t0))
    assert seen == ["c1"]


def test_mailbox_rejects_a_zero_capacity() -> None:
    with pytest.raises(ValueError):
        ConflatingMailbox(max_items=0)
