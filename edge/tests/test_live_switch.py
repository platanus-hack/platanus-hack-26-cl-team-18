"""The console's "En vivo" switch on the agent: hello, status, paused vision, closed mic."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import websockets

from agent.backend_link import BackendLink
from agent.contract import AgentHello, AgentRole, AudienceSnapshot, LiveStatus
from agent.live import LiveGate
from agent.speech.live_feed import feed_while_listening
from agent.vision.pipeline import VisionConfig, VisionPipeline
from agent.vision.tracking import PersonTracker
from agent.vision.types import FrameDetections
from tests.test_vision import FAST, frame, person

pytestmark = pytest.mark.anyio

T0 = datetime(2026, 11, 21, 15, tzinfo=UTC)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def status(*, sensing: bool, listening: bool, screen: str = "demo-01", rev: int = 1) -> LiveStatus:
    return LiveStatus(
        screen_id=screen,
        revision=rev,
        live=sensing,
        mic=listening,
        speech_opt_in=True,
        sensing=sensing,
        listening=listening,
        ai_generation=sensing,
        changed_at=T0,
    )


# ---------------------------------------------------------------- gate


async def test_gate_follows_only_its_own_screen() -> None:
    gate = LiveGate("demo-01", initial=False)
    assert not gate.sensing and not gate.listening
    gate.apply(status(sensing=True, listening=True, screen="other"))
    assert not gate.sensing
    gate.apply(status(sensing=True, listening=False))
    assert gate.sensing and not gate.listening


async def test_gate_adopts_a_restarted_backend_whose_revision_counts_from_zero() -> None:
    gate = LiveGate("demo-01", initial=True)
    gate.apply(status(sensing=False, listening=False, rev=7))
    gate.apply(status(sensing=True, listening=True, rev=0))
    assert gate.sensing and gate.listening


async def test_waiting_resumes_when_the_switch_turns_on() -> None:
    gate = LiveGate("demo-01", initial=False)
    waiter = asyncio.create_task(gate.wait_sensing())
    await asyncio.sleep(0)
    assert not waiter.done()
    gate.apply(status(sensing=True, listening=False))
    await asyncio.wait_for(waiter, 1)


# ---------------------------------------------------------------- backend link


async def test_link_says_hello_first_on_every_connection_and_applies_status() -> None:
    frames: list[str] = []
    connections = 0

    async def handler(ws: websockets.ServerConnection) -> None:
        nonlocal connections
        connections += 1
        first = await ws.recv()
        frames.append(str(first))
        await ws.send(status(sensing=connections > 1, listening=False).model_dump_json())
        if connections == 1:
            await ws.close()  # force a reconnect: the agent must say hello again
            return
        frames.extend([str(message) async for message in ws])

    server = await websockets.serve(handler, "127.0.0.1", 0)
    port = next(iter(server.sockets)).getsockname()[1]
    gate = LiveGate("demo-01", initial=False)
    link = BackendLink(
        f"ws://127.0.0.1:{port}/ws/agent",
        hello=AgentHello(screen_id="demo-01", role=AgentRole.VISION),
        on_status=gate.apply,
        backoff_base_s=0.01,
        backoff_max_s=0.02,
    )
    task = asyncio.create_task(link.run())
    try:
        async with asyncio.timeout(3):
            while not gate.sensing:
                await asyncio.sleep(0.01)
        hellos = [json.loads(f) for f in frames]
        assert [h["type"] for h in hellos] == ["agent.hello", "agent.hello"]
        assert hellos[0]["role"] == "vision" and hellos[0]["screen_id"] == "demo-01"
    finally:
        task.cancel()
        server.close()
        await server.wait_closed()


# ---------------------------------------------------------------- vision


class _Link:
    def __init__(self) -> None:
        self.sent: list[Any] = []

    def send(self, message: Any, *, binary: bytes | None = None) -> None:
        self.sent.append(message)


class _Camera:
    """A live camera: a new frame on every poll, newest only."""

    def __init__(self) -> None:
        self.serial = 0
        self.at = T0

    def latest(self, after_serial: int) -> tuple[Any, datetime, int] | None:
        self.serial += 1
        self.at += timedelta(milliseconds=100)
        return object(), self.at, self.serial


class _Detector:
    name = "fake"

    def __init__(self) -> None:
        self.calls = 0

    def detect(self, _frame: object, measured_at: datetime) -> FrameDetections:
        self.calls += 1
        return frame(measured_at, person(400))


async def _run_for(pipeline: VisionPipeline, seconds: float) -> None:
    task = asyncio.create_task(pipeline.run())
    await asyncio.sleep(seconds)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_vision_in_standby_detects_and_publishes_nothing() -> None:
    gate = LiveGate("demo-01", initial=False)
    link, detector = _Link(), _Detector()
    pipeline = VisionPipeline(
        screen_id="demo-01",
        source=_Camera(),
        detector=detector,
        link=link,
        live=gate,
        config=VisionConfig(standby_poll_s=0.01),
    )
    await _run_for(pipeline, 0.1)
    assert detector.calls == 0 and link.sent == []
    assert pipeline.stats.standby_frames > 0 and pipeline.stats.pauses == 1


async def test_vision_resumes_forgets_the_room_and_keeps_counting_seq() -> None:
    gate = LiveGate("demo-01", initial=True)
    link, detector = _Link(), _Detector()
    tracker = PersonTracker(FAST)
    pipeline = VisionPipeline(
        screen_id="demo-01",
        source=_Camera(),
        detector=detector,
        link=link,
        tracker=tracker,
        live=gate,
        config=VisionConfig(standby_poll_s=0.01, idle_sleep_s=0.001),
    )
    task = asyncio.create_task(pipeline.run())
    try:
        async with asyncio.timeout(3):
            while not any(isinstance(m, AudienceSnapshot) for m in link.sent):
                await asyncio.sleep(0.01)
            gate.apply(status(sensing=False, listening=False))
            while pipeline.stats.pauses == 0:
                await asyncio.sleep(0.01)
            calls, before = detector.calls, len(link.sent)
            await asyncio.sleep(0.1)
            assert detector.calls == calls and len(link.sent) == before  # paused
            gate.apply(status(sensing=True, listening=False, rev=2))
            while len(link.sent) == before:
                await asyncio.sleep(0.01)
            resumed = link.sent[before]
            paused_at = max(m.measured_at for m in link.sent[:before])
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    snapshots = [m for m in link.sent if isinstance(m, AudienceSnapshot)]
    seqs = [s.seq for s in snapshots]
    assert seqs == sorted(set(seqs))  # the backend's duplicate filter accepts every one
    assert isinstance(resumed, AudienceSnapshot) and resumed.measured_at > paused_at
    # A fresh track: its dwell counts from the resume, not from before the pause.
    assert all(
        t.dwell_s <= (resumed.measured_at - paused_at).total_seconds() for t in resumed.tracklets
    )
    assert resumed.tracklets and all(t.dwell_s < 1.0 for t in resumed.tracklets)


# ---------------------------------------------------------------- speech


class _Mic:
    """An endless mic; counts how often the capture stream was opened and closed."""

    def __init__(self) -> None:
        self.opened = 0
        self.closed = 0
        self.discontinuities = 0

    async def frames(self) -> AsyncIterator[bytes]:
        self.opened += 1
        try:
            while True:
                await asyncio.sleep(0.005)
                yield b"\x00\x00" * 160
        finally:
            self.closed += 1


async def test_the_mic_is_closed_in_standby_and_a_cut_phrase_is_discarded() -> None:
    gate = LiveGate("demo-01", initial=False)
    mic, fed, cuts = _Mic(), [], []
    feeder = asyncio.create_task(
        feed_while_listening(mic, gate, on_frame=fed.append, on_cut=lambda: cuts.append(1))
    )
    try:
        await asyncio.sleep(0.05)
        assert mic.opened == 0 and fed == []  # never opened before the switch

        gate.apply(status(sensing=True, listening=True))
        async with asyncio.timeout(2):
            while len(fed) < 3:
                await asyncio.sleep(0.005)
        assert mic.opened == 1

        gate.apply(status(sensing=True, listening=False, rev=2))
        async with asyncio.timeout(2):
            while mic.closed == 0:
                await asyncio.sleep(0.005)
        assert cuts == [1]  # the buffered phrase is dropped, not transcribed
        count = len(fed)
        await asyncio.sleep(0.05)
        assert len(fed) == count and mic.opened == 1  # closed and staying closed

        gate.apply(status(sensing=True, listening=True, rev=3))
        async with asyncio.timeout(2):
            while len(fed) == count:
                await asyncio.sleep(0.005)
        assert mic.opened == 2
    finally:
        feeder.cancel()
        with pytest.raises(asyncio.CancelledError):
            await feeder
    assert mic.closed == 2


async def test_a_wav_replay_ends_when_the_file_ends() -> None:
    class _Wav:
        async def frames(self) -> AsyncIterator[bytes]:
            for _ in range(3):
                yield b"\x00\x00"

    fed: list[bytes] = []
    gate = LiveGate("demo-01", initial=True)
    await asyncio.wait_for(
        feed_while_listening(_Wav(), gate, on_frame=fed.append, on_cut=lambda: None), 1
    )
    assert len(fed) == 3
