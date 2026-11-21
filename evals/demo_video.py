"""The demo video, beat by beat: docs/demo.md §3 run end to end on a live backend.

This is the recording we show judges, executed as a test. Every beat drives
the real loop (agent socket → audience → auction → creative studio → player →
playback report → delivery → redemption) and checks what the screen and the
console would show on camera:

  2. Basket ad: two looking + bottle + cups → Voltio + Café Patio, Dúo $5.990;
     a third person → Trío $8.490, with full coverage beating the solo brand.
  3. Complete the basket: the Café Patio cup marker → the held-product pitch,
     same approved Trío price.
  4. Bidding war: Rayo's slider goes up → Rayo takes Voltio's place next to
     Café Patio, and the two rivals never share the basket.
  5. Close → receipt → $0: the ad reaches its close with a QR, the phone
     redeems it and both brands are credited, the receipt bills attention;
     the group walks away and an unwatched play is charged $0.

Every paid creative must also be a *complete ad*: AI copy on air (replayed
Claude latency), a voice-over (the real ElevenLabs adapter against the sandbox
server), a video loop, logos from the brand kit and an approved price. No
provider credit is spent: keys are never read (evals.live).
"""

from __future__ import annotations

import json
import shutil
import tempfile
import time
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.contract.messages import (
    ConsoleBook,
    ConsoleRedemption,
    CreativeReady,
    CreativeUpgraded,
    DetectedObject,
)
from app.modules.creative.application.loops import LOOP_DIR, bundle_key
from app.modules.creative.infrastructure.elevenlabs_tts import ElevenLabsVoiceSynthesizer
from evals.live import LiveBackend, live_backend
from evals.pipeline import (
    SNAPSHOT_HZ,
    LatencyFaithfulCopywriter,
    MomentTrace,
    TimedVoice,
    _Journey,
    recorded_copy_latencies_ms,
    sandbox_tts,
)
from evals.scenario import Scene

# The props on the counter (docs/demo.md §1): the gym bottle and two coffee cups.
_COUNTER = (
    DetectedObject(label="bottle", score=1),
    DetectedObject(label="cup", score=0.65),
    DetectedObject(label="cup", score=0.65),
)
_MARKER = DetectedObject(label="product:cafe-patio", score=0.95)
COMPETITORS = {frozenset({"Voltio", "Rayo"})}


def _scene(name: str, people: int, *, looking: int | None = None, held: bool = False) -> Scene:
    objects = _COUNTER + ((_MARKER,) if held else ()) if people else ()
    return Scene(
        name=name,
        people=people,
        looking=people if looking is None else looking,
        objects=objects,
    )


@dataclass
class BeatCheck:
    beat: str
    name: str
    passed: bool
    detail: str = ""


@dataclass
class DemoRun:
    checks: list[BeatCheck] = field(default_factory=list)
    recording: list[str] = field(default_factory=list)
    timeline: list[dict[str, Any]] = field(default_factory=list)
    duration_s: float = 0.0

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)


class _Demo(_Journey):
    """The pipeline journey session plus the beat helpers the script needs."""

    def __init__(self, backend: LiveBackend, run: DemoRun) -> None:
        super().__init__(backend)
        self.backend = backend
        self.run = run
        self.redemptions: list[ConsoleRedemption] = []
        self.recording: list[str] = []

    def _handle(self, moment: MomentTrace, message: Any, now: float) -> None:
        # The same JSONL a live rehearsal records, so evals.beats scores both alike.
        channel = "player" if isinstance(message, CreativeReady | CreativeUpgraded) else "console"
        self.recording.append(
            json.dumps(
                {
                    "recv_at": datetime.now(UTC).isoformat(),
                    "channel": channel,
                    "message": message.model_dump(mode="json"),
                }
            )
        )
        if isinstance(message, ConsoleRedemption):
            self.redemptions.append(message)
        super()._handle(moment, message, now)

    def hold(self, scene: Scene, seconds: float, moment: MomentTrace) -> None:
        """Stream the scene at the agent's rate for `seconds` (people stay put)."""
        end = time.perf_counter() + seconds
        tick = 1 / SNAPSHOT_HZ
        while (now := time.perf_counter()) < end:
            self.snapshot(scene, now - moment.sent)
            self.pump(moment, min(end, now + tick))

    def wait_for(self, moment: MomentTrace, done: Any, scene: Scene, limit_s: float) -> bool:
        """Keep the scene live until `done()` holds or `limit_s` passes."""
        end = time.perf_counter() + limit_s
        while time.perf_counter() < end:
            if done():
                return True
            self.hold(scene, 0.2, moment)
        return bool(done())

    def http(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        request = urllib.request.Request(
            f"{self.backend.http_url}{path}",
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Content-Type": "application/json"},
            method=method,
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.loads(response.read())

    def check(self, beat: str, name: str, passed: bool, detail: str = "") -> bool:
        self.run.checks.append(BeatCheck(beat, name, bool(passed), "" if passed else detail))
        return bool(passed)

    def brands(self) -> frozenset[str]:
        on_air = self.on_air
        if on_air is None or on_air.auction_id is None:
            return frozenset()
        return frozenset(slot.brand_name for slot in on_air.spec.slots)

    def copy(self) -> str:
        on_air = self.on_air
        if on_air is None:
            return ""
        spec = on_air.spec
        return " ".join([s.copy_text for s in spec.stages] + [s.copy_text for s in spec.slots])

    def complete(self) -> bool:
        """AI copy and its voice-over on air for a paid creative."""
        on_air = self.on_air
        if on_air is None or on_air.auction_id is None:
            return False
        spec = on_air.spec
        ai = spec.copy_source is not None and spec.copy_source.value in ("ai", "cache")
        return ai and spec.voiceover_url is not None

    def ad_is_complete(self, beat: str) -> None:
        """What a judge sees: real logos, an approved price, motion, voice, AI copy, a QR."""
        on_air = self.on_air
        if not self.check(beat, "paid creative on air", on_air is not None and bool(self.brands())):
            return
        assert on_air is not None
        spec = on_air.spec
        self.check(
            beat,
            "brand-kit logos",
            all(s.logo_url.startswith("/campaign-assets/logos/") for s in spec.slots),
            str([s.logo_url for s in spec.slots]),
        )
        self.check(beat, "video loop", spec.video_url is not None, "no video_url")
        slugs = [s.brand_name.lower().replace(" ", "-").replace("é", "e") for s in spec.slots]
        expected = f"/{LOOP_DIR}/{bundle_key(slugs)}.mp4"
        self.check(
            beat,
            "this basket's own loop (make bundle-loops-local)",
            (spec.video_url or "").endswith(expected),
            f"want …{expected}, got {spec.video_url}",
        )
        self.check(
            beat,
            "teaser → offer → close",
            [s.name.value for s in spec.stages]
            == [
                "teaser",
                "offer",
                "close",
            ],
            str([s.name for s in spec.stages]),
        )
        self.check(beat, "approved group offer", spec.offer is not None, "no offer")
        self.check(
            beat,
            "QR + redemption code",
            spec.qr_url is not None and spec.redemption_code is not None,
            f"qr={spec.qr_url!r} code={spec.redemption_code!r}",
        )
        self.check(
            beat,
            "AI copy + voice-over on air",
            self.complete(),
            f"copy_source={spec.copy_source} voice={spec.voiceover_url}",
        )
        if spec.offer is not None:
            price = f"${int(Decimal(spec.offer.price.amount)):,}".replace(",", ".")
            self.check(beat, "copy states the approved price", price in self.copy(), price)
        rivals = [pair for pair in COMPETITORS if pair <= self.brands()]
        self.check(beat, "no competitors in one basket", not rivals, str(rivals))

    def mark(self, beat: str, moment: MomentTrace) -> None:
        on_air = self.on_air
        self.run.timeline.append(
            {
                "beat": beat,
                "t_s": round(time.perf_counter() - self.started, 2),
                "brands": sorted(self.brands()),
                "offer": on_air.spec.offer.label if on_air and on_air.spec.offer else None,
                "price": on_air.spec.offer.price.amount if on_air and on_air.spec.offer else None,
                "teaser": on_air.spec.stages[0].copy_text if on_air and on_air.spec.stages else "",
                "voice": bool(on_air and on_air.spec.voiceover_url),
                "video": on_air.spec.video_url if on_air else None,
                "ready_ms": None if moment.ready_ms is None else round(moment.ready_ms),
                "complete_ms": None if moment.complete_ms is None else round(moment.complete_ms),
            }
        )

    started: float = 0.0


def run_demo_video(*, copy_scale: float = 1.0, tts_latency_ms: float = 350.0) -> DemoRun:
    run = DemoRun()
    started = time.perf_counter()
    copywriter = LatencyFaithfulCopywriter(recorded_copy_latencies_ms(), scale=copy_scale, seed=18)
    with sandbox_tts() as (base_url, key, voice_id):
        voice = TimedVoice(
            ElevenLabsVoiceSynthesizer(key, base_url=base_url), latency_ms=tts_latency_ms, seed=18
        )
        with (
            tempfile.TemporaryDirectory(prefix="demo-video-media-") as tmp,
            live_backend(
                media_dir=_media_with_loops(Path(tmp)),
                auction_swap_holds=True,  # the recording shows production timing (D5)
                copywriter=copywriter,
                voice_synthesizer=voice,
                creative_ai_copy=True,
                elevenlabs_narrator_voice_id=voice_id,
                elevenlabs_char_budget=100_000,
            ) as backend,
        ):
            demo = _Demo(backend, run)
            demo.started = started
            try:
                _script(demo)
            finally:
                demo.close()
                run.recording = demo.recording
    run.duration_s = time.perf_counter() - started
    return run


LOOPS = Path("var/media") / LOOP_DIR


def _media_with_loops(tmp: Path) -> Path:
    """A throwaway media dir holding the installed bundle loops (never write to var/media)."""
    media = tmp / "media"
    if LOOPS.is_dir():
        shutil.copytree(LOOPS, media / LOOP_DIR)
    return media


def _moment(name: str) -> MomentTrace:
    return MomentTrace(index=0, scene=name, sent=time.perf_counter())


def _script(demo: _Demo) -> None:
    boot = _moment("boot")
    demo.pump(boot, time.perf_counter() + 0.3)
    demo.check("0. never blank", "house ad on air at boot", demo.on_air is not None, "blank")

    # -- Beat 2: basket ad, the group grows -------------------------------------------
    duo = _scene("duo", 2)
    beat = "2. Basket ad · Dúo"
    moment = _moment(beat)
    demo.wait_for(moment, demo.complete, duo, 6.0)
    demo.check(
        beat,
        "Voltio + Café Patio win together",
        demo.brands() == {"Voltio", "Café Patio"},
        str(sorted(demo.brands())),
    )
    demo.check(beat, "Dúo $5.990", "$5.990" in demo.copy(), demo.copy()[:160])
    duo_auction = demo.auctions[-1] if demo.auctions else None
    demo.check(
        beat,
        "basket beats the single-brand counterfactual",
        duo_auction is not None
        and duo_auction.bundle_revenue is not None
        and duo_auction.single_brand_revenue is not None
        and Decimal(duo_auction.bundle_revenue.amount)
        > Decimal(duo_auction.single_brand_revenue.amount),
        "no lift",
    )
    demo.ad_is_complete(beat)
    demo.mark(beat, moment)

    trio = _scene("trio", 3)
    beat = "2. Basket ad · Trío"
    moment = _moment(beat)
    demo.wait_for(moment, lambda: "$8.490" in demo.copy() and demo.complete(), trio, 6.0)
    demo.check(
        beat,
        "same two brands",
        demo.brands() == {"Voltio", "Café Patio"},
        str(sorted(demo.brands())),
    )
    demo.check(beat, "Trío $8.490 for the third person", "$8.490" in demo.copy(), demo.copy()[:160])
    teaser = demo.on_air.spec.stages[0].copy_text if demo.on_air and demo.on_air.spec.stages else ""
    duo_teaser = run_teaser(demo.run, "2. Basket ad · Dúo")
    demo.check(
        beat,
        "the hook itself names the bigger group",
        "tres" in teaser and teaser != duo_teaser,
        f"duo={duo_teaser!r} trio={teaser!r}",
    )
    demo.ad_is_complete(beat)
    demo.mark(beat, moment)

    # -- Beat 3: complete the basket ---------------------------------------------------
    held = _scene("held", 3, held=True)
    beat = "3. Complete the basket"
    moment = _moment(beat)
    demo.wait_for(moment, lambda: "le falta Voltio" in demo.copy() and demo.complete(), held, 6.0)
    demo.check(
        beat,
        "held cup changes the pitch",
        "Café Patio le falta Voltio" in demo.copy(),
        demo.copy()[:160],
    )
    demo.check(beat, "keeps the approved Trío price", "$8.490" in demo.copy(), demo.copy()[:160])
    demo.ad_is_complete(beat)
    demo.mark(beat, moment)

    # -- Beat 4: bidding war -----------------------------------------------------------
    beat = "4. Bidding war"
    moment = _moment(beat)
    book = ConsoleBook.model_validate(demo.http("GET", "/api/book"))
    rayo = next(line for line in book.lines if line.brand_name == "Rayo")
    outcome = demo.http(
        "POST",
        "/api/advertiser/bid-multiplier",
        {"campaign_id": rayo.campaign_id, "multiplier": "1.5", "seq": 1},
    )
    demo.check(beat, "slider applied", outcome.get("outcome") == "applied", str(outcome))
    # The swap waits for the current teaser (minimum ad seconds), then Rayo takes over.
    demo.wait_for(moment, lambda: "Rayo" in demo.brands() and demo.complete(), held, 10.0)
    demo.check(
        beat,
        "Rayo takes Voltio's seat",
        demo.brands() == {"Rayo", "Café Patio"},
        str(sorted(demo.brands())),
    )
    last = demo.auctions[-1] if demo.auctions else None
    name_of = {line.campaign_id: line.brand_name for line in book.lines}
    excluded = {
        name_of.get(e.campaign_id, e.campaign_id): e.reason.value
        for e in (last.exclusions if last else [])
    }
    demo.check(
        beat,
        "console shows the competitor exclusion",
        excluded.get("Voltio") == "competitor",
        str(excluded),
    )
    demo.ad_is_complete(beat)
    demo.mark(beat, moment)

    # -- Beat 5: close → QR → redeem → receipt → walk away -----------------------------
    beat = "5. Close · redeem · receipt"
    moment = _moment(beat)
    demo.hold(held, 8.5, moment)  # dwell past 8 s: the player shows the close stage + QR
    on_air: CreativeReady | None = demo.on_air
    code = on_air.spec.redemption_code if on_air else None
    redeemed: dict[str, Any] = {}
    if demo.check(beat, "close stage carries a code", code is not None, "no code"):
        page = urllib.request.urlopen(f"{demo.backend.http_url}/r/{code}", timeout=5).read()
        demo.check(
            beat,
            "phone offer page shows the code",
            str(code).encode() in page,
            page[:120].decode(errors="replace"),
        )
        redeemed = demo.http("POST", f"/r/{code}/redeem", {"nonce": "demo-video-nonce-1"})
        demo.check(beat, "phone redeems the QR", redeemed.get("redeemed") is True, str(redeemed))
        again = demo.http("POST", f"/r/{code}/redeem", {"nonce": "demo-video-nonce-1"})
        demo.check(beat, "a double tap is not a second sale", again.get("new") is False, str(again))
    demo.pump(moment, time.perf_counter() + 0.5)
    demo.check(
        beat,
        "redemption credited to every brand in the basket",
        bool(demo.redemptions) and set(demo.redemptions[-1].brands) == set(demo.brands()),
        str([r.brands for r in demo.redemptions]),
    )
    paid_receipts = len(demo.receipts)
    walk_away = _scene("walk-away", 0)
    demo.hold(walk_away, 4.0, moment)  # D5.5 grace (3 s), then the house ad; play reported
    receipt = next(
        (r for _, r in demo.receipts[paid_receipts:] if Decimal(r.total.amount) > 0), None
    )
    demo.check(
        beat,
        "receipt bills both brands for attention",
        receipt is not None
        and {c.brand_name for c in receipt.charges} == {"Rayo", "Café Patio"}
        and all(c.reason == "attention" for c in receipt.charges),
        str(receipt.charges if receipt else demo.receipts[-3:]),
    )
    demo.check(
        beat, "screen frees itself (house ad)", not demo.brands(), str(sorted(demo.brands()))
    )
    demo.mark(beat, moment)

    # "charged $0: nobody looked": a paid basket nobody watches.
    beat = "5. Charged $0"
    moment = _moment(beat)
    demo.wait_for(moment, lambda: bool(demo.brands()), _scene("glance", 3), 3.0)
    before = len(demo.receipts)
    demo.hold(_scene("looked-away", 3, looking=0), 1.5, moment)
    demo.hold(walk_away, 4.0, moment)
    zero = [r for _, r in demo.receipts[before:]]
    demo.check(
        beat,
        "an unwatched play is charged $0",
        bool(zero)
        and all(Decimal(r.total.amount) == 0 or r.attention_seconds != "0.000" for r in zero)
        and any(Decimal(r.total.amount) == 0 for r in zero),
        str([(r.attention_seconds, r.total.amount) for r in zero]),
    )
    demo.mark(beat, moment)


def run_teaser(run: DemoRun, beat: str) -> str:
    return next((str(row["teaser"]) for row in run.timeline if row["beat"] == beat), "")


def render(run: DemoRun) -> str:
    lines = [
        "# Demo video: the five beats, end to end",
        "",
        f"**{'PASS' if run.passed else 'FAIL'}** · {sum(c.passed for c in run.checks)}/"
        f"{len(run.checks)} checks · {run.duration_s:.1f} s",
        "",
        "| Beat | t (s) | On screen | Offer | Teaser | Voice | Video | Ready (ms) | Complete (ms) |",
        "|---|---:|---|---|---|:-:|---|---:|---:|",
    ]
    for row in run.timeline:
        lines.append(
            f"| {row['beat']} | {row['t_s']} | {' + '.join(row['brands']) or 'house'} | "
            f"{row['offer'] or '—'} {('$' + row['price']) if row['price'] else ''} | "
            f"{row['teaser'][:48]} | {'✓' if row['voice'] else '—'} | "
            f"{(row['video'] or '—').rsplit('/', 1)[-1]} | {row['ready_ms'] or '—'} | "
            f"{row['complete_ms'] or '—'} |"
        )
    failed = [c for c in run.checks if not c.passed]
    lines += ["", "## Checks", ""]
    lines += [f"- ❌ **{c.beat}** · {c.name}: {c.detail}" for c in failed] or ["- ✅ all passed"]
    return "\n".join(lines) + "\n"
