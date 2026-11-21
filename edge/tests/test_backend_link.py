from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

import pytest
import websockets

from agent.backend_link import BackendLink
from agent.contract import AudienceKeyframe, AudienceUtterance

pytestmark = pytest.mark.anyio

T0 = datetime(2026, 11, 21, 15, tzinfo=UTC)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def utterance(seq: int, text: str = "qué hambre") -> AudienceUtterance:
    return AudienceUtterance(
        screen_id="demo-01",
        seq=seq,
        measured_at=T0,
        text=text,
        language="es",
        keyterms_hit=["hambre"],
    )


class FakeBackend:
    def __init__(self, *, reject: bool = False) -> None:
        self.frames: list[str | bytes] = []
        self.reject = reject
        self.got = asyncio.Event()
        self.server: websockets.Server | None = None

    async def handler(self, ws: websockets.ServerConnection) -> None:
        async for frame in ws:
            self.frames.append(frame)
            self.got.set()
            if self.reject and isinstance(frame, str):
                await ws.send(
                    json.dumps(
                        {
                            "type": "system.error",
                            "v": 1,
                            "code": "message.invalid",
                            "message": "nope",
                            "rejected_type": "audience.utterance",
                        }
                    )
                )

    async def start(self, port: int = 0) -> str:
        self.server = await websockets.serve(self.handler, "127.0.0.1", port)
        sock = next(iter(self.server.sockets))
        return f"ws://127.0.0.1:{sock.getsockname()[1]}/ws/agent"

    async def stop(self) -> None:
        assert self.server is not None
        self.server.close()
        await self.server.wait_closed()


async def wait_for(predicate, timeout: float = 3.0) -> None:  # type: ignore[no-untyped-def]
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.01)


async def test_delivers_messages_in_order_with_binary_after_its_header() -> None:
    backend = FakeBackend()
    url = await backend.start()
    link = BackendLink(url)
    task = asyncio.create_task(link.run())
    try:
        link.send(utterance(1))
        jpeg = b"\xff\xd8blurred\xff\xd9"
        header = AudienceKeyframe(screen_id="demo-01", seq=1, measured_at=T0, byte_length=len(jpeg))
        link.send(header, binary=jpeg)
        await wait_for(lambda: len(backend.frames) == 3)
        assert json.loads(backend.frames[0])["type"] == "audience.utterance"
        assert json.loads(backend.frames[1])["type"] == "audience.keyframe"
        assert backend.frames[2] == jpeg
        assert link.stats.sent == 2
    finally:
        task.cancel()
        await backend.stop()


async def test_counts_rejections_without_resending() -> None:
    backend = FakeBackend(reject=True)
    link = BackendLink(await backend.start())
    task = asyncio.create_task(link.run())
    try:
        link.send(utterance(1))
        await wait_for(lambda: link.stats.rejected == 1)
        await asyncio.sleep(0.05)
        assert len(backend.frames) == 1
    finally:
        task.cancel()
        await backend.stop()


async def test_reconnects_after_the_backend_restarts() -> None:
    backend = FakeBackend()
    url = await backend.start()
    port = int(url.split(":")[2].split("/")[0])
    link = BackendLink(url, backoff_base_s=0.02, backoff_max_s=0.05)
    task = asyncio.create_task(link.run())
    try:
        await link.wait_connected(2)
        await backend.stop()
        await wait_for(lambda: not link.connected)
        link.send(utterance(2, "después del corte"))  # queued while offline
        restarted = FakeBackend()
        await restarted.start(port)
        await wait_for(lambda: len(restarted.frames) == 1)
        assert json.loads(restarted.frames[0])["seq"] == 2
        assert link.stats.reconnects >= 1
        await restarted.stop()
    finally:
        task.cancel()


async def test_outbox_is_bounded_and_drops_oldest_while_offline() -> None:
    async def never(url: str):  # type: ignore[no-untyped-def]
        raise OSError("backend down")

    link = BackendLink("ws://unused", connect=never, max_pending=3)
    for seq in range(5):
        link.send(utterance(seq))
    assert link.stats.dropped == 2
    assert [json.loads(item.text)["seq"] for item in link._pending] == [2, 3, 4]


async def test_an_unexpected_link_error_reconnects_instead_of_ending_the_link() -> None:
    """The vision loop never awaits the link task: if it died, snapshots would pile up unsent."""
    backend = FakeBackend()
    url = await backend.start()
    attempts = 0

    async def flaky_connect(target: str):  # type: ignore[no-untyped-def]
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("unexpected failure inside the client library")
        return await websockets.connect(target)

    link = BackendLink(url, connect=flaky_connect, backoff_base_s=0.01, backoff_max_s=0.02)
    task = asyncio.create_task(link.run())
    try:
        link.send(utterance(1))
        await wait_for(lambda: len(backend.frames) == 1)
        assert not task.done()
    finally:
        task.cancel()
        await backend.stop()
