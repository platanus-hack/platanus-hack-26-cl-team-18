"""Drive a live backend and record what the player and console actually received."""

from __future__ import annotations

import json
import random
import time
from collections.abc import Sequence
from contextlib import ExitStack
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path

from websockets.sync.client import ClientConnection

from app.bootstrap.demo import DEMO_SCREEN
from app.contract.messages import AudienceSnapshot, ConsoleAuction, ConsoleMoment, CreativeReady
from evals.live import CONSOLE_MESSAGES, PLAYER_MESSAGES, LiveBackend, live_backend
from evals.metrics import Decision
from evals.scenario import Expect, Scenario, Scene

RESPONSE_TIMEOUT_S = 2.0
QUIET_WINDOW_S = 0.25  # how long "no_change" waits to be sure nothing arrives
SETTLE_S = 0.1  # after a non-swap decision, how long to confirm the screen stayed put


@dataclass(frozen=True, slots=True, kw_only=True)
class Check:
    source: str
    step: int
    name: str
    passed: bool
    detail: str = ""


@dataclass(slots=True, kw_only=True)
class RunRecord:
    source: str
    decisions: list[Decision] = field(default_factory=list)
    checks: list[Check] = field(default_factory=list)
    initial: CreativeReady | None = None
    duration_s: float = 0.0
    errors: list[str] = field(default_factory=list)
    moments: dict[int, ConsoleMoment] = field(default_factory=dict)  # step -> understood moment


class _Session:
    """One agent, one player and one console socket against a fresh backend.

    Tracks what is on screen. A decision (``console.auction``) does not always
    swap the creative: the engine may keep the incumbent, find the basket
    unchanged, or hold the swap until ``swap_not_before``. Expectations are
    therefore checked against the screen, not against message arrival.
    """

    def __init__(self, backend: LiveBackend) -> None:
        self._sockets = ExitStack()
        self.player: ClientConnection = self._sockets.enter_context(backend.player(DEMO_SCREEN))
        self.agent: ClientConnection = self._sockets.enter_context(backend.agent())
        self.console: ClientConnection = self._sockets.enter_context(backend.console())
        self.seq = 0
        self.initial = self._next_creative(RESPONSE_TIMEOUT_S)
        self.screen = self.initial
        self.last_moment: ConsoleMoment | None = None  # what the audience context understood

    def close(self) -> None:
        self._sockets.close()

    def send(self, snapshot: AudienceSnapshot) -> float:
        started = time.perf_counter()
        self.agent.send(snapshot.model_dump_json())
        return started

    def next_snapshot(self, scene: Scene, offset: timedelta = timedelta(0)) -> AudienceSnapshot:
        self.seq += 1
        return scene.snapshot(
            screen_id=DEMO_SCREEN, seq=self.seq, measured_at=datetime.now(UTC) + offset
        )

    def _next_creative(self, timeout: float) -> CreativeReady:
        while True:
            message = PLAYER_MESSAGES.validate_json(self.player.recv(timeout=timeout))
            if isinstance(message, CreativeReady):
                return message

    def await_creative(self, timeout: float) -> CreativeReady | None:
        try:
            return self._next_creative(max(timeout, 0.0))
        except TimeoutError:
            return None

    def await_auction(self, timeout: float) -> ConsoleAuction | None:
        deadline = time.monotonic() + timeout
        while (remaining := deadline - time.monotonic()) > 0:
            try:
                message = CONSOLE_MESSAGES.validate_json(self.console.recv(timeout=remaining))
            except TimeoutError:
                return None
            if isinstance(message, ConsoleMoment):
                self.last_moment = message
            elif isinstance(message, ConsoleAuction):
                return message
        return None

    def drain_console(self) -> None:
        try:
            while True:
                message = CONSOLE_MESSAGES.validate_json(self.console.recv(timeout=0.01))
                if isinstance(message, ConsoleMoment):
                    self.last_moment = message
        except TimeoutError:
            return

    def observe(self, sent: float, *, quiet: bool) -> Observed:
        """Wait for the decision this snapshot caused, then for the swap it promised."""
        auction = self.await_auction(QUIET_WINDOW_S if quiet else RESPONSE_TIMEOUT_S)
        swap = getattr(auction, "swap", None) if auction is not None else None
        if auction is None:
            creative = self.await_creative(SETTLE_S)  # debounced, stale, or no decision
        elif swap in (None, "swap", "released"):
            creative = self.await_creative(RESPONSE_TIMEOUT_S)  # pushed now
        elif swap == "held":
            not_before = getattr(auction.trace, "swap_not_before", None)
            hold = (not_before - datetime.now(UTC)).total_seconds() if not_before else 0.0
            creative = self.await_creative(hold + RESPONSE_TIMEOUT_S)
        else:  # unchanged / keep_incumbent: the screen intentionally stays
            creative = self.await_creative(SETTLE_S)
        round_trip = (time.perf_counter() - sent) * 1000 if creative is not None else None
        if creative is not None:
            self.screen = creative
        self.drain_console()
        return Observed(auction=auction, swap=swap, creative=creative, round_trip_ms=round_trip)


@dataclass(frozen=True, slots=True, kw_only=True)
class Observed:
    auction: ConsoleAuction | None
    swap: str | None
    creative: CreativeReady | None  # the new creative, if the screen changed
    round_trip_ms: float | None


def _decision(source: str, step: int, minor: bool, session: _Session, seen: Observed) -> Decision:
    return Decision(
        source=source,
        step=step,
        minor_likely=minor,
        creative=session.screen,
        auction=seen.auction,
        swapped=seen.creative is not None,
        swap=seen.swap,
        round_trip_ms=seen.round_trip_ms,
    )


def run_scenario(scenario: Scenario) -> RunRecord:
    record = RunRecord(source=scenario.name)
    started = time.perf_counter()
    with live_backend() as backend:
        session = _Session(backend)
        record.initial = session.initial
        try:
            for index, step in enumerate(scenario.steps):
                sent = session.send(session.next_snapshot(step.scene, step.measured_offset))
                seen = session.observe(sent, quiet=step.expect.no_change)
                check = partial(Check, source=scenario.name, step=index)
                if step.expect.no_change:
                    record.checks.append(
                        check(
                            name="no_change",
                            passed=seen.creative is None,
                            detail="" if seen.creative is None else "screen swapped",
                        )
                    )
                    continue
                if seen.auction is None and seen.creative is None:
                    record.checks.append(
                        check(
                            name="decision_arrived", passed=False, detail="no decision within 2 s"
                        )
                    )
                    continue
                decision = _decision(scenario.name, index, step.scene.minor, session, seen)
                record.decisions.append(decision)
                record.checks.extend(_check(scenario.name, index, step.expect, decision))
        finally:
            session.close()
    record.duration_s = time.perf_counter() - started
    return record


def _check(source: str, step: int, expect: Expect, d: Decision) -> list[Check]:
    def check(name: str, passed: bool, detail: str = "") -> Check:
        return Check(source=source, step=step, name=name, passed=passed, detail=detail)

    checks = [check("console_explained", d.auction is not None, "no console.auction")]
    if expect.brands is not None:
        checks.append(
            check(
                "brands",
                d.brands == expect.brands,
                f"got {sorted(d.brands) or 'house ad'}, want {sorted(expect.brands) or 'house ad'}",
            )
        )
    if expect.layout is not None:
        got = d.creative.spec.layout_id
        checks.append(check("layout", got == expect.layout, f"got {got}, want {expect.layout}"))
    if expect.copy_contains is not None:
        text = " ".join(slot.copy_text for slot in d.creative.spec.slots)
        checks.append(check("copy", expect.copy_contains in text, f"copy was {text!r}"))
    if d.auction is not None:
        names = {bid.campaign_id: bid.brand_name for bid in d.auction.bids}
        got_excluded = {
            names.get(e.campaign_id, e.campaign_id): e.reason.value for e in d.auction.exclusions
        }
        for brand, reason in expect.excluded.items():
            checks.append(
                check(
                    f"excluded:{brand}",
                    got_excluded.get(brand) == reason,
                    f"got {got_excluded.get(brand)}, want {reason}",
                )
            )
        if expect.min_coverage is not None:
            checks.append(
                check(
                    "coverage",
                    d.auction.coverage >= expect.min_coverage,
                    f"got {d.auction.coverage:.2f}, want >= {expect.min_coverage:.2f}",
                )
            )
    return checks


def run_soak(scenes: Sequence[Scene], *, steps: int, seed: int) -> RunRecord:
    """Random walk over demo scenes as fast as the loop answers: latency and stability."""
    rng = random.Random(seed)
    record = RunRecord(source=f"soak[{steps}x seed={seed}]")
    started = time.perf_counter()
    with live_backend() as backend:
        session = _Session(backend)
        current: Scene | None = None
        try:
            for index in range(steps):
                options = [scene for scene in scenes if scene != current]
                current = rng.choice(options)
                sent = session.send(session.next_snapshot(current))
                seen = session.observe(sent, quiet=False)
                if seen.auction is None and seen.creative is None:
                    record.errors.append(f"step {index}: no decision for scene {current.name}")
                    continue
                if seen.swap in (None, "swap", "released") and seen.creative is None:
                    record.errors.append(
                        f"step {index}: swap promised, no creative ({current.name})"
                    )
                record.decisions.append(
                    _decision(record.source, index, current.minor, session, seen)
                )
        finally:
            session.close()
    record.duration_s = time.perf_counter() - started
    return record


def replay_recording(path: Path, *, speed: float = 1.0) -> RunRecord:
    """Replay perception output recorded from a real video through the live backend.

    Each JSONL line is ``{"t_s": <seconds since clip start>, "snapshot": {...}}``
    as written by the agent's replay mode (docs/evals.md §4). Timing is kept
    (scaled by ``speed``) so debounce and flicker behave as they would live;
    ``measured_at`` is re-stamped to now because the backend rejects old facts.
    """
    lines = [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    record = RunRecord(source=f"replay:{path.name}")
    started = time.perf_counter()
    with live_backend() as backend:
        session = _Session(backend)
        record.initial = session.initial
        try:
            clip_start = time.perf_counter()
            for index, line in enumerate(lines):
                due = clip_start + float(line["t_s"]) / speed
                if (delay := due - time.perf_counter()) > 0:
                    time.sleep(delay)
                original = AudienceSnapshot.model_validate(line["snapshot"])
                session.seq += 1
                # Rebase every device boundary together, preserving sensing
                # duration without leaving historical candidate timestamps behind.
                shift = datetime.now(UTC) - (original.published_at or original.measured_at)
                instants = {
                    field: value + shift if value is not None else None
                    for field in ("measured_at", "candidate_at", "detected_at", "published_at")
                    for value in (getattr(original, field),)
                }
                snapshot = original.model_copy(
                    update={"screen_id": DEMO_SCREEN, "seq": session.seq, **instants}
                )
                sent = session.send(snapshot)
                seen = session.observe(sent, quiet=True)
                if seen.auction is None and seen.creative is None:
                    continue
                record.decisions.append(
                    _decision(record.source, index, snapshot.minor_likely.value, session, seen)
                )
                if session.last_moment is not None:
                    record.moments[index] = session.last_moment
        finally:
            session.close()
    record.duration_s = time.perf_counter() - started
    return record
