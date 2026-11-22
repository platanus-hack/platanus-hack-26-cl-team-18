"""Venue-network drills: the real loop through fault proxies, measured end to end.

    uv run python -m evals.network_stress                 # every drill, table on stdout
    uv run python -m evals.network_stress partition_all   # one drill by name

Topology (ADR 0008 D3/D7), each arrow through its own :class:`evals.netfault.FaultProxy`:

    agent (real edge BackendLink) -> backend <- player (TV contract mimic)
                                     backend <- console (laptop B, ?live=1)
                                     backend <- phones (/r/{code}, bids)  [phone drill]

The player and console mimic the browser: no WebSocket pings from the client
side (browsers never send them), an 8 s connect timeout and capped backoff like
``web/src/lib/ws/typed-socket.ts``. `/health` is polled on a direct link to
measure event-loop responsiveness. All instants share one machine clock.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, contextmanager, suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import httpx
from scripts.replay_audience import snapshot_for
from websockets.exceptions import ConnectionClosed
from websockets.sync.client import ClientConnection, connect

from app.bootstrap.demo import DEMO_SCREEN
from app.contract.messages import (
    AgentHello,
    AgentRole,
    ConsoleReceipt,
    ConsoleRedemption,
    CreativeReady,
    PlaybackReport,
    PlayerDisplayed,
)
from evals.live import CONSOLE_MESSAGES, PLAYER_MESSAGES, live_backend
from evals.netfault import FaultProxy

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "edge"))
from agent.backend_link import BackendLink

SCENES = ("co-brand", "fitness", "adult", "co-brand", "empty")
SCENE_S = 3.0
SNAPSHOT_HZ = 5.0
# web/src/lib/ws/heartbeat.ts (0 = the pre-fix client: no idle watchdog).
IDLE_S = float(os.environ.get("IDLE_S", "15"))


def now() -> float:
    return time.time()


def pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(q * (len(ordered) - 1)))]


# ---------------------------------------------------------------- agent


class Agent:
    """The real edge BackendLink, fed a scripted room at SNAPSHOT_HZ."""

    def __init__(self, url: str) -> None:
        self.url = url
        self.seq = 0
        self.scene_log: list[tuple[float, str]] = []
        self._stop = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self.link: BackendLink | None = None
        self.scene_override: str | None = None

    def scene_at(self, t: float, t0: float) -> str:
        if self.scene_override is not None:
            return self.scene_override
        return SCENES[int((t - t0) / SCENE_S) % len(SCENES)]

    def run(self, t0: float) -> threading.Thread:
        def main() -> None:
            async def go() -> None:
                self.link = BackendLink(
                    self.url,
                    hello=AgentHello(screen_id=DEMO_SCREEN, role=AgentRole.VISION),
                )
                task = asyncio.create_task(self.link.run())
                while not self._stop.is_set():
                    t = now()
                    scene = self.scene_at(t, t0)
                    self.seq += 1
                    self.scene_log.append((t, scene))
                    self.link.send(snapshot_for(scene, self.seq, datetime.fromtimestamp(t, UTC)))
                    await asyncio.sleep(1 / SNAPSHOT_HZ)
                task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await task

            asyncio.run(go())

        thread = threading.Thread(target=main, name="agent", daemon=True)
        thread.start()
        return thread

    def stop(self) -> None:
        self._stop.set()


# ---------------------------------------------------------------- browser-like clients


@dataclass
class Aired:
    at: float
    creative_id: str
    auction_id: str | None
    measured_at: float | None
    received_at: float | None
    cleared_at: float | None
    catch_up: bool  # a resend on (re)connect, not a new decision


@dataclass
class ClientLog:
    connects: int = 0
    down_since: float | None = None
    down_s: float = 0.0
    last_frame_at: float = 0.0
    errors: list[str] = field(default_factory=list)


class BrowserSocket:
    """TypedSocket semantics: 8 s handshake timeout, equal-jitter backoff, no pings."""

    def __init__(
        self,
        url: str,
        on_message: Callable[[ClientConnection, str], None],
        *,
        idle_s: float | None = None,
    ) -> None:
        self.url = url
        self.idle_s = idle_s if idle_s is not None else IDLE_S
        self.on_message = on_message
        self.log = ClientLog()
        self._stop = threading.Event()
        self.ws: ClientConnection | None = None

    def run(self) -> threading.Thread:
        thread = threading.Thread(target=self._main, daemon=True)
        thread.start()
        return thread

    def stop(self) -> None:
        self._stop.set()
        if self.ws is not None:
            with suppress(Exception):
                self.ws.close()

    def _main(self) -> None:
        attempt = 0
        self.log.down_since = now()
        while not self._stop.is_set():
            try:
                ws = connect(
                    self.url, open_timeout=8, ping_interval=None, close_timeout=1, legacy=True
                )
            except Exception as exc:  # refused, timed out, reset
                attempt += 1
                self.log.errors.append(type(exc).__name__)
                time.sleep(min(5.0, 0.25 * 2 ** (attempt - 1)) * 0.75)
                continue
            attempt = 0
            self.ws = ws
            self.log.connects += 1
            if self.log.down_since is not None:
                self.log.down_s += now() - self.log.down_since
                self.log.down_since = None
            try:
                self.log.last_frame_at = now()
                while not self._stop.is_set():
                    try:
                        raw = ws.recv(timeout=0.5)
                    except TimeoutError:
                        if self.idle_s and now() - self.log.last_frame_at > self.idle_s:
                            # TypedSocket `idleTimeoutMs`: a silent link is a dead link.
                            self.log.errors.append("idle")
                            ws.close()
                            break
                        continue
                    self.log.last_frame_at = now()
                    self.on_message(ws, raw if isinstance(raw, str) else raw.decode())
            except (ConnectionClosed, OSError) as exc:
                self.log.errors.append(type(exc).__name__)
            finally:
                self.ws = None
                if self.log.down_since is None:
                    self.log.down_since = now()
            attempt += 1
            time.sleep(min(5.0, 0.25 * 2 ** (attempt - 1)) * 0.75)
        if self.log.down_since is not None:
            self.log.down_s += now() - self.log.down_since


class Player:
    def __init__(self, ws_url: str) -> None:
        self.aired: list[Aired] = []
        self.seq = 0
        self._current: tuple[str, float] | None = None
        self.socket = BrowserSocket(
            f"{ws_url}/ws/screen/{DEMO_SCREEN}?attention=1&live=1", self._message
        )

    def _message(self, ws: ClientConnection, raw: str) -> None:
        message = PLAYER_MESSAGES.validate_json(raw)
        if not isinstance(message, CreativeReady):
            return
        at = now()
        seen = any(a.creative_id == message.creative_id for a in self.aired)
        trace = message.trace

        def ts(value: datetime | None) -> float | None:
            return value.timestamp() if value is not None else None

        self.aired.append(
            Aired(
                at=at,
                creative_id=message.creative_id,
                auction_id=message.auction_id,
                measured_at=ts(trace.measured_at),
                received_at=ts(trace.received_at),
                cleared_at=ts(trace.cleared_at),
                catch_up=seen,
            )
        )
        if seen:
            return
        self.seq += 1
        displayed = datetime.fromtimestamp(at, UTC)
        with suppress(Exception):
            if self._current is not None:
                previous, started = self._current
                self.seq += 1
                ws.send(
                    PlaybackReport(
                        screen_id=DEMO_SCREEN,
                        creative_id=previous,
                        seq=self.seq,
                        started_at=datetime.fromtimestamp(started, UTC),
                        ended_at=displayed,
                    ).model_dump_json()
                )
            ws.send(
                PlayerDisplayed(
                    screen_id=DEMO_SCREEN,
                    creative_id=message.creative_id,
                    seq=self.seq,
                    displayed_at=displayed,
                ).model_dump_json()
            )
        self._current = (message.creative_id, at)


class Console:
    def __init__(self, ws_url: str) -> None:
        self.receipts: list[ConsoleReceipt] = []
        self.redemptions: list[ConsoleRedemption] = []
        self.types: dict[str, int] = {}
        self.socket = BrowserSocket(f"{ws_url}/ws/console?live=1", self._message)

    def _message(self, _ws: ClientConnection, raw: str) -> None:
        kind = json.loads(raw).get("type", "?")
        self.types[kind] = self.types.get(kind, 0) + 1
        if kind == "dev.perception":
            return
        message = CONSOLE_MESSAGES.validate_json(raw)
        if isinstance(message, ConsoleReceipt):
            self.receipts.append(message)
        elif isinstance(message, ConsoleRedemption):
            self.redemptions.append(message)


class HealthProbe:
    def __init__(self, http_url: str) -> None:
        self.http_url = http_url
        self.samples: list[float] = []
        self.failures = 0
        self._stop = threading.Event()

    def run(self) -> threading.Thread:
        def main() -> None:
            with httpx.Client(timeout=5) as client:
                while not self._stop.is_set():
                    started = time.perf_counter()
                    try:
                        client.get(f"{self.http_url}/health").raise_for_status()
                        self.samples.append((time.perf_counter() - started) * 1000)
                    except Exception:
                        self.failures += 1
                    time.sleep(0.2)

        thread = threading.Thread(target=main, daemon=True)
        thread.start()
        return thread

    def stop(self) -> None:
        self._stop.set()


# ---------------------------------------------------------------- drills


@dataclass
class Drill:
    name: str
    describe: str
    fault: Callable[[dict[str, FaultProxy], Agent], None] | None = None
    heal: Callable[[dict[str, FaultProxy], Agent], None] | None = None
    fault_at_s: float = 8.0
    fault_s: float = 0.0
    total_s: float = 30.0
    steady: Callable[[dict[str, FaultProxy]], None] | None = None  # whole-run impairment


def _lat(ms: float, jitter: float) -> Callable[[dict[str, FaultProxy]], None]:
    def apply(proxies: dict[str, FaultProxy]) -> None:
        for proxy in proxies.values():
            for way in (proxy.up, proxy.down):
                way.latency_s, way.jitter_s = ms / 2000, jitter / 2000  # half per direction

    return apply


def _loss(p: float) -> Callable[[dict[str, FaultProxy]], None]:
    def apply(proxies: dict[str, FaultProxy]) -> None:
        for proxy in proxies.values():
            for way in (proxy.up, proxy.down):
                way.latency_s, way.jitter_s, way.loss, way.rto_s = 0.025, 0.01, p, 0.3

    return apply


def _on(*names: str, attr: str = "partition") -> Callable[[dict[str, FaultProxy], Agent], None]:
    def apply(proxies: dict[str, FaultProxy], _agent: Agent) -> None:
        for name in names:
            proxy = proxies[name]
            if attr == "partition":
                proxy.partition()
            elif attr == "up":
                proxy.up.blocked = True
            elif attr == "down":
                proxy.down.blocked = True
            elif attr == "reset":
                proxy.reset()
            elif attr == "accept_only":
                proxy.accept_only = True
                proxy.reset()

    return apply


def _off(*names: str) -> Callable[[dict[str, FaultProxy], Agent], None]:
    def apply(proxies: dict[str, FaultProxy], _agent: Agent) -> None:
        for name in names:
            proxies[name].heal()
            proxies[name].accept_only = False

    return apply


LINKS = ("agent", "player", "console")


def _flood(rate_hz: float) -> tuple[Callable[..., None], Callable[..., None]]:
    """A misbehaving second agent: snapshots for the same screen as fast as it can."""
    stop = threading.Event()

    def start(proxies: dict[str, FaultProxy], _agent: Agent) -> None:
        stop.clear()

        def main() -> None:
            url = f"ws://127.0.0.1:{proxies['phone'].port}/ws/agent"
            with connect(url, open_timeout=10, ping_interval=None) as ws:
                seq = 10**9
                while not stop.is_set():
                    seq += 1
                    scene = "co-brand" if (seq // 50) % 2 else "fitness"
                    ws.send(snapshot_for(scene, seq, datetime.now(UTC)).model_dump_json())
                    time.sleep(1 / rate_hz)

        threading.Thread(target=main, daemon=True).start()

    def end(_proxies: dict[str, FaultProxy], _agent: Agent) -> None:
        stop.set()

    return start, end


def _cpu_burn() -> tuple[Callable[..., None], Callable[..., None]]:
    """Every core pegged by other processes (a browser, a build, another agent)."""
    procs: list[subprocess.Popen[bytes]] = []

    def start(_proxies: dict[str, FaultProxy], _agent: Agent) -> None:
        for _ in range(os.cpu_count() or 4):
            procs.append(subprocess.Popen([sys.executable, "-c", "while True: pass"]))

    def end(_proxies: dict[str, FaultProxy], _agent: Agent) -> None:
        for proc in procs:
            proc.kill()
        for proc in procs:
            proc.wait(5)
        procs.clear()

    return start, end


_FLOOD = _flood(1000)
_CPU = _cpu_burn()
DRILLS: dict[str, Drill] = {
    d.name: d
    for d in (
        Drill("baseline", "no fault", total_s=20),
        Drill("lat50", "50±10 ms RTT on every link", steady=_lat(50, 10), total_s=20),
        Drill("lat200", "200±50 ms RTT on every link", steady=_lat(200, 50), total_s=20),
        Drill("lat800", "800±200 ms RTT on every link", steady=_lat(800, 200), total_s=25),
        Drill("loss5", "5% chunk stalls (300 ms RTO) + 50 ms RTT", steady=_loss(0.05), total_s=25),
        Drill(
            "loss10", "10% chunk stalls (300 ms RTO) + 50 ms RTT", steady=_loss(0.10), total_s=25
        ),
        Drill(
            "partition_agent_10s",
            "agent link partitioned 10 s",
            _on("agent"),
            _off("agent"),
            fault_s=10,
            total_s=40,
        ),
        Drill(
            "partition_player_10s",
            "TV link partitioned 10 s",
            _on("player"),
            _off("player"),
            fault_s=10,
            total_s=40,
        ),
        Drill(
            "partition_all_30s",
            "every link partitioned 30 s (Wi-Fi gone)",
            _on(*LINKS),
            _off(*LINKS),
            fault_s=30,
            total_s=70,
        ),
        Drill(
            "halfopen_player_down_20s",
            "backend->TV silently stops 20 s",
            _on("player", attr="down"),
            _off("player"),
            fault_s=20,
            total_s=50,
        ),
        Drill(
            "halfopen_console_down_45s",
            "backend->console silently stops 45 s",
            _on("console", attr="down"),
            _off("console"),
            fault_s=45,
            total_s=75,
        ),
        Drill(
            "halfopen_agent_up_20s",
            "agent->backend silently stops 20 s",
            _on("agent", attr="up"),
            _off("agent"),
            fault_s=20,
            total_s=50,
        ),
        Drill(
            "stuck_tunnel_10s",
            "links reset, then accept-but-never-forward 10 s",
            _on(*LINKS, attr="accept_only"),
            _off(*LINKS),
            fault_s=10,
            total_s=40,
        ),
        Drill(
            "agent_flood_1khz",
            "a second agent floods 1000 snapshots/s for 15 s",
            _FLOOD[0],
            _FLOOD[1],
            fault_s=15,
            total_s=35,
        ),
        Drill(
            "cpu_saturated",
            "every core pegged by busy loops for 15 s",
            _CPU[0],
            _CPU[1],
            fault_s=15,
            total_s=35,
        ),
        Drill(
            "ap_reboot",
            "every connection reset (AP reboot)",
            _on(*LINKS, attr="reset"),
            None,
            fault_s=0,
            total_s=25,
        ),
    )
}


@dataclass
class Result:
    name: str
    describe: str
    g2g_p50: float | None
    g2g_p95: float | None
    decision_p50: float | None
    decision_p95: float | None
    health_p50: float | None
    health_p95: float | None
    health_max: float | None
    recovery_s: float | None
    stale_airs: int
    time_travel: int
    player_down_s: float
    console_down_s: float
    receipts: int
    dup_receipts: int
    console_billed: float
    backend_spent: float
    blank_s: float
    console_lied_s: float  # socket "open" yet no frame for > 5 s while the backend sent

    def row(self) -> str:
        def f(v: float | None, d: int = 0) -> str:
            return "-" if v is None else f"{v:.{d}f}"

        return (
            f"| {self.name} | {f(self.g2g_p50)}/{f(self.g2g_p95)} | "
            f"{f(self.decision_p50, 1)}/{f(self.decision_p95, 1)} | "
            f"{f(self.health_p50, 1)}/{f(self.health_p95, 1)}/{f(self.health_max)} | "
            f"{f(self.recovery_s, 1)} | {self.stale_airs} | {self.time_travel} | "
            f"{self.player_down_s:.1f}/{self.console_down_s:.1f} | "
            f"{self.receipts}/{self.dup_receipts} | "
            f"{self.console_billed:.0f}/{self.backend_spent:.0f} | {self.blank_s:.1f} | "
            f"{self.console_lied_s:.1f} |"
        )


HEADER = (
    "| drill | g2g ms p50/p95 | decision ms p50/p95 | /health ms p50/p95/max | recovery s "
    "| stale airs | time travel | down s TV/console | receipts/replayed | billed console/backend "
    "| blank s | console stale-but-open s |\n|" + "---|" * 12
)


def _spent(http_url: str) -> float:
    book = httpx.get(f"{http_url}/api/book", timeout=5).json()
    return sum(float(line["spent"]["amount"]) for line in book["lines"])


@contextmanager
def _backend_process() -> Iterator[str]:
    """The backend in its own process (one uvicorn worker, like the station), so the
    drill's clients, proxies and threads don't share its GIL and skew its latency."""
    proc = subprocess.Popen(
        [sys.executable, "-m", "evals.network_stress", "serve"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        cwd=Path(__file__).resolve().parents[1],
    )
    try:
        assert proc.stdout is not None
        url = proc.stdout.readline().strip()
        if not url.startswith("http://"):
            raise RuntimeError("drill backend failed to start")
        yield url
    finally:
        with suppress(Exception):
            assert proc.stdin is not None
            proc.stdin.close()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()


def _serve() -> int:
    with live_backend(auction_swap_holds=False) as backend:
        print(backend.http_url, flush=True)
        sys.stdin.read()  # until the drill closes our stdin
    return 0


@contextmanager
def _topology() -> Iterator[tuple[str, dict[str, FaultProxy]]]:
    with ExitStack() as stack:
        http_url = stack.enter_context(_backend_process())
        host, port = http_url.removeprefix("http://").split(":")
        proxies = {
            name: stack.enter_context(FaultProxy.running(host, int(port), seed=index))
            for index, name in enumerate((*LINKS, "phone"))
        }
        yield http_url, proxies


def run_drill(drill: Drill) -> Result:
    with _topology() as (http_url, proxies):
        ws = {name: f"ws://127.0.0.1:{proxy.port}" for name, proxy in proxies.items()}
        if drill.steady is not None:
            drill.steady(proxies)
        t0 = now()
        agent = Agent(f"{ws['agent']}/ws/agent")
        player = Player(ws["player"])
        console = Console(ws["console"])
        health = HealthProbe(http_url)
        threads = [player.socket.run(), console.socket.run(), health.run(), agent.run(t0)]
        fault_start = t0 + drill.fault_at_s
        fault_end = fault_start + drill.fault_s
        faulted = healed = False
        lied = 0.0
        while (t := now()) < t0 + drill.total_s:
            if drill.fault is not None and not faulted and t >= fault_start:
                drill.fault(proxies, agent)
                faulted = True
            if drill.heal is not None and faulted and not healed and t >= fault_end:
                drill.heal(proxies, agent)
                healed = True
            # The console believes it is live (socket open) while frames stopped arriving.
            if console.socket.ws is not None and t - console.socket.log.last_frame_at > 5:
                lied += 0.1
            time.sleep(0.1)
        for thing in (agent, player.socket, console.socket, health):
            thing.stop()
        for thread in threads:
            thread.join(3)
        backend_spent = _spent(http_url)

    # The web player is latest-wins (player-machine.ts): a creative replaced within
    # its preload never reaches the glass, so a burst counts once, as its last item.
    arrivals = player.aired
    shown = [
        a
        for i, a in enumerate(arrivals)
        if i + 1 == len(arrivals) or arrivals[i + 1].at - a.at > 0.3
    ]
    fresh = [a for a in shown if not a.catch_up and a.measured_at is not None]
    g2g = [(a.at - a.measured_at) * 1000 for a in fresh if a.measured_at]
    decision = [
        (a.cleared_at - a.received_at) * 1000 for a in fresh if a.cleared_at and a.received_at
    ]
    stale = [a for a in fresh if a.measured_at and a.at - a.measured_at > 3.0]
    travel, newest = 0, 0.0
    for a in fresh:
        if a.measured_at is not None and a.measured_at < newest - 0.001:
            travel += 1
        newest = max(newest, a.measured_at or 0.0)
    recovery = None
    if drill.fault is not None:
        mark = fault_end if drill.fault_s else fault_start
        after = [a for a in fresh if a.measured_at and a.measured_at >= mark and a.at >= mark]
        recovery = (after[0].at - mark) if after else None
    ids = [r.creative_id for r in console.receipts]
    # Count like the web reducer (console-state.ts): a replayed receipt once.
    unique = {
        (r.creative_id, r.played_s, r.attention_seconds, r.total.amount): r
        for r in console.receipts
    }
    blank = (player.aired[0].at - t0) if player.aired else drill.total_s
    return Result(
        name=drill.name,
        describe=drill.describe,
        g2g_p50=pct(g2g, 0.5),
        g2g_p95=pct(g2g, 0.95),
        decision_p50=pct(decision, 0.5),
        decision_p95=pct(decision, 0.95),
        health_p50=pct(health.samples, 0.5),
        health_p95=pct(health.samples, 0.95),
        health_max=max(health.samples) if health.samples else None,
        recovery_s=recovery,
        stale_airs=len(stale),
        time_travel=travel,
        player_down_s=player.socket.log.down_s,
        console_down_s=console.socket.log.down_s,
        receipts=len(ids),
        dup_receipts=len(ids) - len(set(ids)),
        console_billed=sum(float(r.total.amount) for r in unique.values()),
        backend_spent=backend_spent,
        blank_s=max(0.0, blank - 1.0),  # the first second is the boot handshake
        console_lied_s=lied,
    )


# ---------------------------------------------------------------- phones


@dataclass
class PhoneResult:
    phones: int
    requests: int
    status: dict[int, int]
    timeouts: int
    redeem_new: int
    distinct_nonces: int
    console_redemptions: int
    bid_final_ok: bool
    p50_ms: float | None
    p95_ms: float | None
    health_max: float | None


def run_phones(phones: int = 40, *, kbit: float = 256, rtt_ms: float = 300) -> PhoneResult:
    """N judges on bad 4G redeem the same basket and drag one bid slider at once."""
    with _topology() as (http_url, proxies):
        phone = proxies["phone"]
        for way in (phone.up, phone.down):
            way.latency_s, way.jitter_s = rtt_ms / 2000, rtt_ms / 6000
            way.bandwidth_bps = kbit * 1000
            way.loss, way.rto_s = 0.03, 0.4
        ws_agent = f"ws://127.0.0.1:{proxies['agent'].port}/ws/agent"
        t0 = now()
        agent = Agent(ws_agent)
        agent.scene_override = "co-brand"
        player = Player(f"ws://127.0.0.1:{proxies['player'].port}")
        console = Console(f"ws://127.0.0.1:{proxies['console'].port}")
        health = HealthProbe(http_url)
        threads = [player.socket.run(), console.socket.run(), health.run(), agent.run(t0)]
        code = None
        deadline = now() + 15
        while code is None and now() < deadline:
            time.sleep(0.2)
            for a in reversed(player.aired):
                if a.auction_id:
                    code = "pending"
                    break
        # Read the code from the creative the player holds (the QR's target).
        book_lines = httpx.get(f"{http_url}/api/book", timeout=5).json()["lines"]
        code = _current_code(http_url, proxies)
        base = f"http://127.0.0.1:{phone.port}"
        statuses: dict[int, int] = {}
        timeouts = 0
        latencies: list[float] = []
        new = 0
        lock = threading.Lock()
        campaign = book_lines[0]["campaign_id"]
        nonces = [uuid.uuid4().hex for _ in range(phones)]

        def judge(index: int) -> None:
            nonlocal timeouts, new
            with httpx.Client(timeout=2.0) as client:
                # Open the offer page, then redeem; a timeout is retried with the SAME
                # nonce (what the page's button does on a second tap).
                for path, body in (
                    (f"/r/{code}", None),
                    (f"/r/{code}/redeem", {"nonce": nonces[index]}),
                    (f"/r/{code}/redeem", {"nonce": nonces[index]}),  # double tap
                ):
                    for _attempt in range(4):
                        started = time.perf_counter()
                        try:
                            r = (
                                client.get(base + path)
                                if body is None
                                else client.post(base + path, json=body)
                            )
                        except httpx.TimeoutException:
                            with lock:
                                timeouts += 1
                            continue
                        except httpx.HTTPError:
                            with lock:
                                timeouts += 1
                            continue
                        with lock:
                            latencies.append((time.perf_counter() - started) * 1000)
                            statuses[r.status_code] = statuses.get(r.status_code, 0) + 1
                            if body is not None and r.status_code == 200 and r.json().get("new"):
                                new += 1
                        break
                # Drag the slider: seqs increase; requests race on separate connections.
                for step in range(3):
                    seq = int(time.time() * 1000) * 100 + index * 3 + step
                    value = f"{1 + ((index + step) % 30) / 10:.1f}"
                    for _attempt in range(3):
                        try:
                            r = client.post(
                                base + "/api/advertiser/bid-multiplier",
                                json={
                                    "type": "advertiser.bid_multiplier",
                                    "v": 1,
                                    "campaign_id": campaign,
                                    "multiplier": value,
                                    "seq": seq,
                                },
                            )
                        except httpx.HTTPError:
                            with lock:
                                timeouts += 1
                            continue
                        with lock:
                            statuses[r.status_code] = statuses.get(r.status_code, 0) + 1
                        break

        with ThreadPoolExecutor(phones) as pool:
            list(pool.map(judge, range(phones)))
        # The last write wins by seq: one more bid with the highest seq must stick.
        final_seq = int(time.time() * 1000) * 100 + 10_000
        httpx.post(
            f"{http_url}/api/advertiser/bid-multiplier",
            json={
                "type": "advertiser.bid_multiplier",
                "v": 1,
                "campaign_id": campaign,
                "multiplier": "4.2",
                "seq": final_seq,
            },
            timeout=5,
        ).raise_for_status()
        time.sleep(1.0)
        line = next(
            item
            for item in httpx.get(f"{http_url}/api/book", timeout=5).json()["lines"]
            if item["campaign_id"] == campaign
        )
        for thing in (agent, player.socket, console.socket, health):
            thing.stop()
        for thread in threads:
            thread.join(3)
    return PhoneResult(
        phones=phones,
        requests=sum(statuses.values()),
        status=statuses,
        timeouts=timeouts,
        redeem_new=new,
        distinct_nonces=phones,
        console_redemptions=len(console.redemptions),
        bid_final_ok=line["multiplier"] in {"4.2", "4.20"} and line["multiplier_seq"] == final_seq,
        p50_ms=pct(latencies, 0.5),
        p95_ms=pct(latencies, 0.95),
        health_max=max(health.samples) if health.samples else None,
    )


def _current_code(http_url: str, proxies: dict[str, FaultProxy]) -> str:
    """The redemption code on air (a direct, unimpaired player read)."""
    with connect(
        http_url.replace("http", "ws") + f"/ws/screen/{DEMO_SCREEN}", open_timeout=5
    ) as ws:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            message = PLAYER_MESSAGES.validate_json(ws.recv(timeout=10))
            if isinstance(message, CreativeReady) and message.spec.redemption_code:
                return message.spec.redemption_code
    raise RuntimeError("no basket with a redemption code aired")


def main(argv: list[str]) -> int:
    names = [a for a in argv if not a.startswith("-")]
    if names == ["serve"]:
        return _serve()
    if "phones" in names:
        for kbit, rtt in ((256, 300), (64, 800)):
            r = run_phones(int(os.environ.get("PHONES", "40")), kbit=kbit, rtt_ms=rtt)
            print(
                f"phones={r.phones} {kbit}kbit/{rtt}ms: requests={r.requests} status={r.status} "
                f"client_timeouts={r.timeouts} redeem_new={r.redeem_new}/{r.distinct_nonces} "
                f"console_redemptions={r.console_redemptions} final_bid_sticks={r.bid_final_ok} "
                f"latency p50/p95={r.p50_ms}/{r.p95_ms} ms health_max={r.health_max} ms",
                flush=True,
            )
        names.remove("phones")
        if not names:
            return 0
    drills = [DRILLS[n] for n in names] if names else list(DRILLS.values())
    print(HEADER, flush=True)
    for drill in drills:
        result = run_drill(drill)
        print(result.row(), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
