"""A fault-injecting TCP proxy for venue-network drills (no root, no tc/netem).

Put it between any client and the backend (agent, player, console, phones, GPU):

    with FaultProxy.running("127.0.0.1", backend_port) as proxy:
        connect(f"ws://127.0.0.1:{proxy.port}/ws/screen/demo-01")
        proxy.down.latency_s = 0.2          # backend -> client gets 200 ms slower
        proxy.partition()                    # both directions stall (Wi-Fi gone)
        proxy.heal()                         # buffered bytes flow again, in order
        proxy.up.blocked = True              # half-open: the client's writes vanish
        proxy.reset()                        # every connection dies (RST-like close)
        proxy.accept_only = True             # accepts, never forwards (stuck tunnel)

TCP is a byte stream, so a direction never reorders: every chunk is released at
``max(previous release, arrival + latency + jitter)``, plus its serialization
time under a bandwidth cap. "Loss" is what loss looks like above TCP: a chunk
(and everything behind it) stalls for a retransmission timeout. A partition
keeps reading into a bounded buffer and then stops reading, so the sender's
kernel buffer fills and its writes block, exactly like a dead Wi-Fi link that
has not reset the connection yet. Burst reordering only exists across
connections (two phones, or a socket and its reconnect); drive it with two
proxies with different latencies.

Runs its own event loop on a daemon thread so synchronous drivers
(``websockets.sync``, ``httpx``) can use it. Deterministic given ``seed``.
"""

from __future__ import annotations

import asyncio
import random
import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field

CHUNK = 16 * 1024
BUFFER_CHUNKS = 16  # ~256 KiB held per direction before the proxy stops reading


@dataclass(slots=True)
class Fault:
    """One direction's impairment; mutate it live from any thread."""

    latency_s: float = 0.0
    jitter_s: float = 0.0
    loss: float = 0.0  # probability that a chunk stalls for `rto_s` (a retransmission)
    rto_s: float = 0.2
    bandwidth_bps: float | None = None  # bits per second, None = unlimited
    blocked: bool = False  # hold every byte (partition / half-open direction)

    def clear(self) -> None:
        self.latency_s = self.jitter_s = self.loss = 0.0
        self.rto_s = 0.2
        self.bandwidth_bps = None
        self.blocked = False


@dataclass(slots=True)
class ProxyStats:
    connections: int = 0
    bytes_up: int = 0
    bytes_down: int = 0
    stalls: int = 0
    resets: int = 0


@dataclass(eq=False)
class _Link:
    client: asyncio.StreamWriter
    server: asyncio.StreamWriter | None = None
    tasks: list[asyncio.Task[None]] = field(default_factory=list)


class FaultProxy:
    def __init__(self, target_host: str, target_port: int, *, seed: int = 0) -> None:
        self.target = (target_host, target_port)
        self.up = Fault()  # client -> server
        self.down = Fault()  # server -> client
        self.accept_only = False
        self.stats = ProxyStats()
        self._random = random.Random(seed)
        self._links: set[_Link] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._server: asyncio.Server | None = None
        self._thread: threading.Thread | None = None
        self._sock: socket.socket | None = None
        self.port = 0

    # ------------------------------------------------------------ control (any thread)

    def partition(self) -> None:
        self.up.blocked = self.down.blocked = True

    def heal(self) -> None:
        self.up.blocked = self.down.blocked = False

    def clear(self) -> None:
        self.up.clear()
        self.down.clear()
        self.accept_only = False

    def reset(self) -> None:
        """Abort every live connection (what a NAT or AP reboot does)."""
        loop = self._loop
        if loop is None:
            return
        done = threading.Event()

        def abort() -> None:
            for link in list(self._links):
                self._abort(link)
            done.set()

        loop.call_soon_threadsafe(abort)
        done.wait(2)

    @property
    def open_connections(self) -> int:
        return len(self._links)

    # ------------------------------------------------------------ lifecycle

    @classmethod
    @contextmanager
    def running(cls, target_host: str, target_port: int, *, seed: int = 0) -> Iterator[FaultProxy]:
        proxy = cls(target_host, target_port, seed=seed)
        proxy.start()
        try:
            yield proxy
        finally:
            proxy.stop()

    def start(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", 0))
        self._sock = sock
        self.port = sock.getsockname()[1]
        ready = threading.Event()

        def run() -> None:
            loop = asyncio.new_event_loop()
            self._loop = loop

            async def main() -> None:
                self._server = await asyncio.start_server(self._accept, sock=sock)
                ready.set()
                await self._server.serve_forever()

            with suppress(asyncio.CancelledError):
                loop.run_until_complete(main())
            loop.close()

        self._thread = threading.Thread(target=run, name="fault-proxy", daemon=True)
        self._thread.start()
        if not ready.wait(5):
            raise RuntimeError("fault proxy failed to start")

    def stop(self) -> None:
        loop, server = self._loop, self._server
        if loop is None or server is None:
            return

        def shutdown() -> None:
            for link in list(self._links):
                self._abort(link)
            server.close()
            for task in asyncio.all_tasks(loop):
                task.cancel()

        with suppress(RuntimeError):
            loop.call_soon_threadsafe(shutdown)
        if self._thread is not None:
            self._thread.join(5)

    # ------------------------------------------------------------ data path

    async def _accept(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.stats.connections += 1
        link = _Link(client=writer)
        self._links.add(link)
        try:
            if self.accept_only:
                # A stuck tunnel: the handshake bytes go nowhere, nothing comes back.
                while not reader.at_eof():
                    if not await reader.read(CHUNK):
                        break
                return
            try:
                up_reader, up_writer = await asyncio.open_connection(*self.target)
            except OSError:
                return
            link.server = up_writer
            up = asyncio.create_task(self._pump(reader, up_writer, self.up, "up"))
            down = asyncio.create_task(self._pump(up_reader, writer, self.down, "down"))
            link.tasks = [up, down]
            await asyncio.wait(link.tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            self._abort(link)

    async def _pump(
        self, src: asyncio.StreamReader, dst: asyncio.StreamWriter, fault: Fault, way: str
    ) -> None:
        queue: asyncio.Queue[tuple[float, bytes] | None] = asyncio.Queue(BUFFER_CHUNKS)

        async def read() -> None:
            released = 0.0
            while True:
                data = await src.read(CHUNK)
                if not data:
                    await queue.put(None)
                    return
                now = time.monotonic()
                delay = fault.latency_s + (self._random.uniform(-1, 1) * fault.jitter_s)
                if fault.loss and self._random.random() < fault.loss:
                    delay += fault.rto_s
                    self.stats.stalls += 1
                released = max(released, now + max(0.0, delay))
                await queue.put((released, data))

        reader = asyncio.create_task(read())
        sent_until = 0.0
        try:
            while True:
                item = await queue.get()
                if item is None:
                    with suppress(OSError):
                        if dst.can_write_eof():
                            dst.write_eof()
                    return
                release, data = item
                while (wait := release - time.monotonic()) > 0:
                    await asyncio.sleep(min(wait, 0.05))
                while fault.blocked:
                    await asyncio.sleep(0.02)
                if fault.bandwidth_bps:
                    start = max(time.monotonic(), sent_until)
                    sent_until = start + len(data) * 8 / fault.bandwidth_bps
                    # Serialize at the capped rate in small slices, so a large frame
                    # trickles out the way it would over a slow radio link.
                    step = max(1, int(fault.bandwidth_bps / 8 / 50))  # 20 ms slices
                    for offset in range(0, len(data), step):
                        piece = data[offset : offset + step]
                        dst.write(piece)
                        await dst.drain()
                        await asyncio.sleep(len(piece) * 8 / fault.bandwidth_bps)
                        while fault.blocked:
                            await asyncio.sleep(0.02)
                else:
                    dst.write(data)
                    await dst.drain()
                if way == "up":
                    self.stats.bytes_up += len(data)
                else:
                    self.stats.bytes_down += len(data)
        except OSError, ConnectionError:
            return
        finally:
            reader.cancel()

    def _abort(self, link: _Link) -> None:
        if link not in self._links:
            return
        self._links.discard(link)
        self.stats.resets += 1
        for task in link.tasks:
            task.cancel()
        for writer in (link.client, link.server):
            if writer is None:
                continue
            transport = writer.transport
            with suppress(Exception):
                transport.abort()
