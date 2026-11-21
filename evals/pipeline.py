"""End-to-end ad pipeline journeys: see → auction → compose → AI copy → voice → play → bill.

    uv run python -m evals pipeline                  # make eval-pipeline
    uv run python -m evals pipeline --tts-latency-ms 600 --copy-scale 2   # stress a provider

A real backend runs with the *whole* generation stack: the creative studio
(guard, score, copy cache, ROI budget), the warm worker, the voice-over
service with its media cache and character budget, the real ElevenLabs HTTP
adapter (against the in-process sandbox server, tests/sandbox/fake_elevenlabs.py),
attention tracking and billing. The eval plays agent, player and console
through a demo-paced timeline (5 Hz snapshots with live tracklets, a dwell
per moment, a playback report at the end) and timestamps every stage.

Providers are the only replayed parts, and their timing is faithful:
copy latency is drawn from the recorded real Claude calls
(evals/creative/recordings.json), and TTS latency is an explicit knob
(``--tts-latency-ms``) until a live measurement replaces it. Everything
around them (queues, workers, locks, caches, sockets) is the real code.

The report is a waterfall: each stage's p50/p95, its share of the time to
the complete ad, the slowest stage (the bottleneck), waste (upgrades that
arrived after the moment moved on), cache effects, cost and billing gates.
"""

from __future__ import annotations

import asyncio
import json
import queue
import random
import statistics
import threading
import time
from collections.abc import Iterator, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from websockets.exceptions import ConnectionClosed
from websockets.sync.client import ClientConnection

from app.bootstrap.demo import DEMO_SCREEN
from app.contract.messages import (
    ConsoleAuction,
    ConsoleReceipt,
    CreativeReady,
    CreativeUpgraded,
    DistanceBand,
    PlaybackReport,
    Tracklet,
)
from app.modules.creative.application.ports.copywriter import TokenUsage, WrittenCopy
from app.modules.creative.application.ports.voice import (
    SynthesizedVoice,
    VoiceRequest,
    VoiceSynthesizer,
)
from app.modules.creative.application.stock_copy import StockCopywriter
from app.modules.creative.domain.brief import CreativeBrief
from app.modules.creative.domain.draft import CopySource
from app.modules.creative.infrastructure.elevenlabs_tts import ElevenLabsVoiceSynthesizer
from evals.live import CONSOLE_MESSAGES, PLAYER_MESSAGES, LiveBackend, live_backend, serve
from evals.metrics import percentile
from evals.scenario import Scene, load_scenes

RECORDINGS = Path(__file__).resolve().parent / "creative" / "recordings.json"
SNAPSHOT_HZ = 5.0
# Demo journey: a judged run in front of the screen, including a return to an
# earlier group (cache effects) and a walk-away (house ad, charged $0).
JOURNEY = ("co-brand", "fitness", "adult", "minor-safe", "co-brand", "coffee", "co-brand", "empty")

# Budgets for the warm path (docs/architecture.md §3: LLM 0.4–1 s, TTS ~75–100 ms model
# + network). "Complete ad" = copy + voice upgraded on screen after the moment changed.
BUDGETS_MS = {
    "hot_decision": 30.0,
    "copy_upgrade": 1500.0,
    "voice_upgrade": 1000.0,
    "complete_ad": 3000.0,
}


# ----------------------------------------------------------------------------- providers


def recorded_copy_latencies_ms() -> list[float]:
    raw = json.loads(RECORDINGS.read_text(encoding="utf-8"))
    return [float(r["latency_ms"]) for r in raw["recordings"].values() if r.get("latency_ms")]


@dataclass
class ProviderLog:
    calls: list[tuple[str, float, float]] = field(default_factory=list)  # (key, started, ms)

    def add(self, key: str, started: float, ms: float) -> None:
        self.calls.append((key, started, ms))


class LatencyFaithfulCopywriter:
    """Targeted stock lines marked as AI, delivered after a real recorded Claude latency.

    Content is the brief's own stock copy (so the guard passes and the studio's
    accept path runs); timing is a draw from real Claude latencies, so the
    warm path is loaded exactly as it would be live.
    """

    name = "replay-latency"

    def __init__(self, latencies_ms: Sequence[float], *, scale: float, seed: int) -> None:
        self._latencies = list(latencies_ms) or [640.0]
        self._scale = scale
        self._rng = random.Random(seed)
        self._stock = StockCopywriter()
        self.log = ProviderLog()

    async def write(self, brief: CreativeBrief) -> WrittenCopy:
        started = time.perf_counter()
        delay = self._rng.choice(self._latencies) * self._scale / 1000
        await asyncio.sleep(delay)
        stock = self._stock.compose(brief)
        draft = type(stock)(
            teaser=stock.teaser,
            offer=stock.offer,
            close=stock.close,
            voice_line=stock.voice_line,
            source=CopySource.AI,
            model_version="replay-latency",
        )
        self.log.add(brief.signature(), started, (time.perf_counter() - started) * 1000)
        return WrittenCopy(
            draft=draft, usage=TokenUsage(input_tokens=1180, output_tokens=142, estimated=True)
        )


class TimedVoice:
    """The real ElevenLabs adapter (sandbox server) plus an explicit provider latency."""

    def __init__(self, inner: VoiceSynthesizer, *, latency_ms: float, seed: int) -> None:
        self._inner = inner
        self._latency_ms = latency_ms
        self._rng = random.Random(seed)
        self.log = ProviderLog()

    async def synthesize(self, request: VoiceRequest) -> SynthesizedVoice:
        started = time.perf_counter()
        jitter = self._rng.uniform(0.8, 1.4)  # right-skewed, like network tails
        await asyncio.sleep(self._latency_ms * jitter / 1000)
        voice = await self._inner.synthesize(request)
        self.log.add(request.text, started, (time.perf_counter() - started) * 1000)
        return voice


@contextmanager
def sandbox_tts() -> Iterator[tuple[str, str, str]]:
    """The sandbox ElevenLabs server on a loopback port: (base_url, key, voice_id)."""
    from tests.sandbox import fake_elevenlabs

    with serve(fake_elevenlabs.app) as host:
        yield f"http://{host}", fake_elevenlabs.SANDBOX_KEY, fake_elevenlabs.VOICE_ID


# ----------------------------------------------------------------------------- journey


@dataclass
class MomentTrace:
    index: int
    scene: str
    sent: float  # perf_counter at the first snapshot of the moment
    paid: bool = False
    brands: tuple[str, ...] = ()
    creative_id: str | None = None
    server_ms: float | None = None
    ready_ms: float | None = None  # send -> creative.ready at the player
    first_voice_ms: float | None = None  # send -> any voice-over on air (audio starts)
    copy_ms: float | None = None  # send -> AI copy on air (0-latency if cached at ready)
    voice_ms: float | None = None  # send -> voice-over on air for the AI copy
    complete_ms: float | None = None  # send -> AI copy + its voice-over on air
    upgrades: list[tuple[float, str]] = field(default_factory=list)
    late_upgrades: int = 0  # upgrades for a creative no longer on air
    receipt_ms: float | None = None  # playback.report -> console.receipt
    receipt_total: Decimal | None = None
    attention_s: Decimal | None = None
    expected_attention_s: float = 0.0
    played_s: float = 0.0


@dataclass
class JourneyRun:
    moments: list[MomentTrace]
    copy_log: ProviderLog
    voice_log: ProviderLog
    duration_s: float
    config: dict[str, Any]


class _Journey:
    def __init__(self, backend: LiveBackend) -> None:
        self._stack = ExitStack()
        self.player: ClientConnection = self._stack.enter_context(backend.player(DEMO_SCREEN))
        self.agent: ClientConnection = self._stack.enter_context(backend.agent())
        self.console: ClientConnection = self._stack.enter_context(backend.console())
        self.seq = 0
        self.on_air: CreativeReady | None = None
        self.on_air_since = time.perf_counter()
        self.on_air_wall = datetime.now(UTC)
        self.receipts: list[tuple[float, ConsoleReceipt]] = []
        self.reported: dict[str, float] = {}  # creative_id -> perf_counter of its playback.report
        self.auctions: list[ConsoleAuction] = []
        # One reader thread per socket stamps each message the instant it arrives,
        # so stage timings don't inherit the harness's polling interval.
        self._inbox: queue.Queue[tuple[float, str, Any]] = queue.Queue()
        self._readers = [
            threading.Thread(target=self._read, args=(conn, adapter, channel), daemon=True)
            for conn, adapter, channel in (
                (self.player, PLAYER_MESSAGES, "player"),
                (self.console, CONSOLE_MESSAGES, "console"),
            )
        ]
        for reader in self._readers:
            reader.start()

    def _read(self, conn: ClientConnection, adapter: Any, channel: str) -> None:
        try:
            for raw in conn:
                self._inbox.put((time.perf_counter(), channel, adapter.validate_json(raw)))
        except ConnectionClosed:
            return

    def close(self) -> None:
        self._stack.close()
        for reader in self._readers:
            reader.join(timeout=2)

    def snapshot(self, scene: Scene, dwell_s: float) -> None:
        self.seq += 1
        looking = scene.looking
        snap = scene.snapshot(screen_id=DEMO_SCREEN, seq=self.seq, measured_at=datetime.now(UTC))
        tracklets = [
            Tracklet(
                id=f"t{i}",
                dwell_s=round(dwell_s, 2),
                looking=i < looking,
                distance_band=DistanceBand.NEAR,
            )
            for i in range(scene.people)
        ]
        self.agent.send(snap.model_copy(update={"tracklets": tracklets}).model_dump_json())

    def pump(self, moment: MomentTrace, until: float) -> None:
        """Handle messages until ``until`` (perf_counter), stamping each stage at arrival."""
        while (remaining := until - time.perf_counter()) > 0:
            try:
                arrived, channel, message = self._inbox.get(timeout=remaining)
            except queue.Empty:
                return
            # Creatives count only as the player sees them (the console gets copies).
            if channel == "player" or not isinstance(message, CreativeReady | CreativeUpgraded):
                self._handle(moment, message, arrived)

    def _handle(self, moment: MomentTrace, message: Any, now: float) -> None:
        elapsed = (now - moment.sent) * 1000
        if isinstance(message, CreativeReady):
            self._report_play()  # the previous creative left the screen
            self.on_air, self.on_air_since, self.on_air_wall = message, now, datetime.now(UTC)
            if moment.creative_id is None:
                moment.creative_id = message.creative_id
                moment.ready_ms = elapsed
                moment.paid = message.auction_id is not None
                moment.brands = (
                    tuple(sorted(s.brand_name for s in message.spec.slots)) if moment.paid else ()
                )
                trace = message.trace
                if trace.received_at and trace.ready_at:
                    moment.server_ms = (trace.ready_at - trace.received_at).total_seconds() * 1000
                self._stamp(moment, message.spec, elapsed)
        elif isinstance(message, CreativeUpgraded):
            if self.on_air is None or message.creative_id != self.on_air.creative_id:
                moment.late_upgrades += 1
                return
            self.on_air = CreativeReady(
                screen_id=message.screen_id,
                creative_id=message.creative_id,
                auction_id=message.auction_id,
                spec=message.spec,
                trace=message.trace,
            )
            if message.creative_id == moment.creative_id:
                moment.upgrades.append((elapsed, message.upgrade_kind.value))
                self._stamp(moment, message.spec, elapsed)
        elif isinstance(message, ConsoleReceipt):
            self.receipts.append((now, message))
        elif isinstance(message, ConsoleAuction):
            self.auctions.append(message)

    @staticmethod
    def _stamp(moment: MomentTrace, spec: Any, elapsed: float) -> None:
        """Stage times from what is on air, not from message order."""
        ai = spec.copy_source is not None and spec.copy_source.value in ("ai", "cache")
        if spec.voiceover_url and moment.first_voice_ms is None:
            moment.first_voice_ms = elapsed
        if ai and moment.copy_ms is None:
            moment.copy_ms = elapsed
        if ai and spec.voiceover_url and moment.complete_ms is None:
            # The AI line's own voice: a new URL after the copy swap, or the same
            # URL when the AI line speaks the same words (a cache hit).
            moment.voice_ms = moment.complete_ms = elapsed

    def _report_play(self) -> None:
        if self.on_air is None:
            return
        self.seq += 1
        report = PlaybackReport(
            screen_id=DEMO_SCREEN,
            creative_id=self.on_air.creative_id,
            seq=self.seq,
            started_at=self.on_air_wall,
            ended_at=datetime.now(UTC),
        )
        self.reported[self.on_air.creative_id] = time.perf_counter()
        self.player.send(report.model_dump_json())


def run_journey(
    *,
    dwell_s: float = 5.0,
    copy_scale: float = 1.0,
    tts_latency_ms: float = 350.0,
    seed: int = 18,
    journey: Sequence[str] = JOURNEY,
    cold: bool = False,
    warmup: bool = False,
) -> JourneyRun:
    scenes = load_scenes()
    copywriter = LatencyFaithfulCopywriter(
        recorded_copy_latencies_ms(), scale=copy_scale, seed=seed
    )
    started = time.perf_counter()
    with sandbox_tts() as (base_url, key, voice_id):
        voice = TimedVoice(
            ElevenLabsVoiceSynthesizer(key, base_url=base_url), latency_ms=tts_latency_ms, seed=seed
        )
        with live_backend(
            copywriter=copywriter,
            voice_synthesizer=voice,
            creative_ai_copy=True,
            elevenlabs_narrator_voice_id=voice_id,
            elevenlabs_char_budget=100_000,
        ) as backend:
            warmup_s = 0.0 if cold else _await_quiet(voice.log, copywriter.log)
            warmed: list[WarmupResult] = []
            if warmup:
                unique = list(dict.fromkeys(journey))
                warmed = warm_up(backend, [scenes[name] for name in unique])
                _await_quiet(voice.log, copywriter.log)
            prewarm_calls = len(voice.log.calls)
            if not cold:  # the journey measures live calls only
                voice.log.calls.clear()
                copywriter.log.calls.clear()
            session = _Journey(backend)
            moments: list[MomentTrace] = []
            try:
                session.pump(
                    MomentTrace(-1, "boot", time.perf_counter()), time.perf_counter() + 0.3
                )
                for index, name in enumerate(journey):
                    scene = scenes[name]
                    moment = MomentTrace(index=index, scene=name, sent=time.perf_counter())
                    moments.append(moment)
                    receipts_before = len(session.receipts)
                    end = moment.sent + dwell_s
                    tick = 1 / SNAPSHOT_HZ
                    while (now := time.perf_counter()) < end:
                        session.snapshot(scene, now - moment.sent)
                        session.pump(moment, min(end, now + tick))
                    moment.played_s = dwell_s
                    moment.expected_attention_s = scene.looking * dwell_s
                    _attach_receipt(session, moments, receipts_before)
                session._report_play()
                session.pump(moments[-1], time.perf_counter() + 0.5)
                _attach_receipt(session, moments, 0, final=True)
            finally:
                session.close()
    return JourneyRun(
        moments=moments,
        copy_log=copywriter.log,
        voice_log=voice.log,
        duration_s=time.perf_counter() - started,
        config={
            "start": (
                "cold (live right after boot)"
                if cold
                else f"warm (startup prewarm done in {warmup_s:.1f} s)"
                + (f" + demo warm-up of {len(warmed)} scenes" if warmup else "")
            ),
            "warmup": [(w.scene, w.complete_ms, w.paid) for w in warmed],
            "prewarm_voice_calls": prewarm_calls,
            "dwell_s": dwell_s,
            "copy_latency": f"recorded Claude x{copy_scale}",
            "tts_latency_ms": tts_latency_ms,
            "journey": list(journey),
        },
    )


@dataclass(frozen=True, slots=True)
class WarmupResult:
    scene: str
    paid: bool
    voice_ms: float | None  # voice-over on air (now cached on disk)
    copy_ms: float | None  # AI copy on air (now in the copy cache); None if AI copy is off

    @property
    def complete_ms(self) -> float | None:
        if self.voice_ms is None or self.copy_ms is None:
            return None
        return max(self.voice_ms, self.copy_ms)


def warm_up(
    backend: LiveBackend,
    scenes: Sequence[Scene],
    *,
    timeout_s: float = 8.0,
    copy_grace_s: float = 3.0,
) -> list[WarmupResult]:
    """Demo-day warm-up: play each scripted scene until its ad is fully generated.

    Drives the real path once per scene, so the AI copy lands in the copy cache
    (in memory: lost on a backend restart) and every voice-over lands in the
    media cache (on disk: survives restarts). Run it in the same daypart as the
    demo, because the daypart is part of the copy cache key. Without a
    copywriter configured, a scene is done once its voice-over is on air.
    """
    results = []
    with backend.player(DEMO_SCREEN) as player, backend.agent() as agent:
        seq = 0
        for scene in scenes:
            started = time.perf_counter()
            on_air: Any = None
            voice_ms: float | None = None
            copy_ms: float | None = None
            while (elapsed := time.perf_counter() - started) < timeout_s:
                seq += 1
                snap = scene.snapshot(screen_id=DEMO_SCREEN, seq=seq, measured_at=datetime.now(UTC))
                agent.send(snap.model_dump_json())
                deadline = time.perf_counter() + 1 / SNAPSHOT_HZ
                while (left := deadline - time.perf_counter()) > 0:
                    try:
                        message = PLAYER_MESSAGES.validate_json(player.recv(timeout=left))
                    except TimeoutError:
                        break
                    if isinstance(message, CreativeReady | CreativeUpgraded):
                        on_air = message
                now_ms = (time.perf_counter() - started) * 1000
                spec = on_air.spec if on_air is not None else None
                paid = on_air is not None and on_air.auction_id is not None
                if on_air is not None and not paid and elapsed > 1:
                    break  # house ad: nothing to warm
                if not paid or spec is None:
                    continue
                if spec.voiceover_url and voice_ms is None:
                    voice_ms = now_ms
                if spec.copy_source is not None and spec.copy_source.value in ("ai", "cache"):
                    copy_ms = copy_ms or now_ms
                if voice_ms is not None and (
                    copy_ms is not None or now_ms - voice_ms > copy_grace_s * 1000
                ):
                    break
            results.append(
                WarmupResult(
                    scene=scene.name,
                    paid=bool(on_air is not None and on_air.auction_id),
                    voice_ms=voice_ms,
                    copy_ms=copy_ms,
                )
            )
    return results


def _await_quiet(*logs: ProviderLog, quiet_s: float = 1.0, limit_s: float = 60.0) -> float:
    """Wait until startup prewarm stops calling providers (the demo's normal state)."""
    started = time.perf_counter()
    last_count, last_change = -1, started
    while time.perf_counter() - started < limit_s:
        count = sum(len(log.calls) for log in logs)
        if count != last_count:
            last_count, last_change = count, time.perf_counter()
        elif time.perf_counter() - last_change >= quiet_s:
            break
        time.sleep(0.05)
    return time.perf_counter() - started


def _attach_receipt(
    session: _Journey, moments: list[MomentTrace], since: int, *, final: bool = False
) -> None:
    """Receipts arrive for the creative that just left the screen; match by creative id."""
    by_creative = {m.creative_id: m for m in moments if m.creative_id}
    for received, receipt in session.receipts[since:]:
        moment = by_creative.get(receipt.creative_id)
        if moment is None or moment.receipt_total is not None:
            continue
        moment.receipt_total = Decimal(receipt.total.amount)
        moment.attention_s = Decimal(receipt.attention_seconds)
        if (reported := session.reported.get(receipt.creative_id)) is not None:
            moment.receipt_ms = (received - reported) * 1000


# ----------------------------------------------------------------------------- analysis


STAGES = (
    ("hot_decision", "Hot decision (server: received → ready)"),
    ("ready", "Moment → creative on the player"),
    ("first_voice", "Moment → first voice-over on air (audio starts)"),
    ("copy_upgrade", "Moment → AI copy on air"),
    ("voice_upgrade", "AI copy on air → its voice-over on air"),
    ("complete_ad", "Moment → complete ad (copy + voice)"),
    ("billing", "Playback report → receipt"),
)


def analyse(run: JourneyRun) -> dict[str, Any]:
    paid = [m for m in run.moments if m.paid]
    series: dict[str, list[float]] = {
        "hot_decision": [m.server_ms for m in run.moments if m.server_ms is not None],
        "ready": [m.ready_ms for m in run.moments if m.ready_ms is not None],
        "copy_upgrade": [m.copy_ms for m in paid if m.copy_ms is not None],
        "first_voice": [m.first_voice_ms for m in paid if m.first_voice_ms is not None],
        "voice_upgrade": [
            m.voice_ms - m.copy_ms for m in paid if m.voice_ms is not None and m.copy_ms is not None
        ],
        "complete_ad": [m.complete_ms for m in paid if m.complete_ms is not None],
        "billing": [m.receipt_ms for m in run.moments if m.receipt_ms is not None],
    }
    stages = {
        key: {"p50": percentile(values, 50), "p95": percentile(values, 95), "n": len(values)}
        for key, values in series.items()
    }
    complete = stages["complete_ad"]["p50"] or 0.0
    critical = {
        "hot (ready)": stages["ready"]["p50"] or 0.0,
        "copy wait": max(
            0.0, (stages["copy_upgrade"]["p50"] or 0.0) - (stages["ready"]["p50"] or 0.0)
        ),
        "voice wait": max(0.0, stages["voice_upgrade"]["p50"] or 0.0),
    }
    bottleneck = max(critical, key=lambda k: critical[k]) if complete else None

    copy_provider = [ms for _, _, ms in run.copy_log.calls]
    voice_provider = [ms for _, _, ms in run.voice_log.calls]
    # Our own overhead on the copy path, per moment: time on air minus when the
    # provider call finished (queueing, guard, scoring, cache, push, socket).
    overheads = []
    for m in paid:
        if m.copy_ms is None:
            continue
        calls = [
            (started, ms)
            for _, started, ms in run.copy_log.calls
            if m.sent <= started <= m.sent + m.copy_ms / 1000
        ]
        if calls:
            started, ms = calls[0]
            overheads.append(m.copy_ms - ((started - m.sent) * 1000 + ms))
    copy_overhead = statistics.median(overheads) if overheads else None
    repeats = _repeat_effect(run.moments)
    texts = [text for text, _, _ in run.voice_log.calls]
    duplicate_voice = len(texts) - len(set(texts))
    house_billed = [
        m for m in run.moments if not m.paid and m.receipt_total not in (None, Decimal(0))
    ]
    attention_error = [
        abs(float(m.attention_s) - m.expected_attention_s) / m.expected_attention_s
        for m in paid
        if m.attention_s is not None and m.expected_attention_s
    ]
    metrics: dict[str, Any] = {
        "pipeline_moments": len(run.moments),
        "pipeline_paid_moments": len(paid),
        "hot_decision_p95_ms": stages["hot_decision"]["p95"],
        "creative_on_player_p95_ms": stages["ready"]["p95"],
        "copy_upgrade_p50_ms": stages["copy_upgrade"]["p50"],
        "copy_upgrade_p95_ms": stages["copy_upgrade"]["p95"],
        "voice_upgrade_p50_ms": stages["voice_upgrade"]["p50"],
        "voice_upgrade_p95_ms": stages["voice_upgrade"]["p95"],
        "complete_ad_p50_ms": stages["complete_ad"]["p50"],
        "complete_ad_p95_ms": stages["complete_ad"]["p95"],
        "copy_upgrade_rate": _ratio(sum(m.copy_ms is not None for m in paid), len(paid)),
        "voice_upgrade_rate": _ratio(sum(m.voice_ms is not None for m in paid), len(paid)),
        "late_upgrades": sum(m.late_upgrades for m in run.moments),
        "copy_provider_calls": len(copy_provider),
        "voice_provider_calls": len(voice_provider),
        "voice_calls_per_paid_moment": _ratio(len(voice_provider), len(paid)),
        "voice_duplicate_syntheses": duplicate_voice,
        "first_voice_p50_ms": stages["first_voice"]["p50"],
        "copy_pipeline_overhead_ms": copy_overhead,
        "repeat_complete_ad_p50_ms": repeats.get("repeat"),
        "first_complete_ad_p50_ms": repeats.get("first"),
        "billing_receipt_rate": _ratio(sum(m.receipt_total is not None for m in paid), len(paid)),
        "house_ad_billed": len(house_billed),
        "attention_error_mean": statistics.fmean(attention_error) if attention_error else None,
        "billed_total_clp": float(sum((m.receipt_total or Decimal(0)) for m in run.moments)),
    }
    return {
        "stages": stages,
        "critical_path_p50_ms": critical,
        "bottleneck": bottleneck,
        "metrics": metrics,
    }


def _repeat_effect(moments: Sequence[MomentTrace]) -> dict[str, float | None]:
    seen: set[tuple[str, ...]] = set()
    first: list[float] = []
    repeat: list[float] = []
    for m in moments:
        if not m.paid or m.complete_ms is None:
            continue
        (repeat if m.brands in seen else first).append(m.complete_ms)
        seen.add(m.brands)
    return {
        "first": statistics.median(first) if first else None,
        "repeat": statistics.median(repeat) if repeat else None,
    }


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def render(run: JourneyRun, analysis: dict[str, Any]) -> str:
    def fmt(v: Any) -> str:
        return "—" if v is None else (f"{v:.0f}" if isinstance(v, float) else str(v))

    lines = [
        "## Pipeline waterfall (p50 / p95 ms)",
        "",
        f"Start: {run.config['start']} · prewarmed voice lines: {run.config['prewarm_voice_calls']} · "
        f"dwell {run.config['dwell_s']} s per moment · copy latency {run.config['copy_latency']} · "
        f"TTS {run.config['tts_latency_ms']} ms (assumed) · journey {' → '.join(run.config['journey'])}",
        "",
        "| Stage | p50 | p95 | n | Budget |",
        "|---|---:|---:|---:|---:|",
    ]
    for key, label in STAGES:
        stage = analysis["stages"][key]
        budget = BUDGETS_MS.get(key)
        flag = ""
        if budget and stage["p95"] is not None and stage["p95"] > budget:
            flag = " ⚠️"
        lines.append(
            f"| {label} | {fmt(stage['p50'])} | {fmt(stage['p95'])}{flag} | {stage['n']} | {fmt(budget)} |"
        )
    crit = analysis["critical_path_p50_ms"]
    total = sum(crit.values()) or 1
    lines += [
        "",
        "**Critical path to the complete ad (p50):** "
        + " → ".join(f"{name} {ms:.0f} ms ({ms / total:.0%})" for name, ms in crit.items())
        + f". **Bottleneck: {analysis['bottleneck']}.**",
        "",
        "| Moment | Scene | Brands | Ready | Copy | Voice | Late | Billed | AS (want) |",
        "|---:|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for m in run.moments:
        lines.append(
            f"| {m.index} | {m.scene} | {' + '.join(m.brands) or 'house'} | {fmt(m.ready_ms)} | {fmt(m.copy_ms)} | "
            f"{fmt(m.voice_ms)} | {m.late_upgrades} | {fmt(m.receipt_total)} | {fmt(m.attention_s)} ({m.expected_attention_s:.0f}) |"
        )
    return "\n".join(lines) + "\n"
