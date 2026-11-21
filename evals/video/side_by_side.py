"""Real footage beside the real ad it produced: the end-to-end flow, judged by eye and by checks.

    uv run --with playwright python -m evals.video.side_by_side          # every clip
    uv run --with playwright python -m evals.video.side_by_side --only cafe-group-meeting

For each clip in clips.json whose agent recording exists (`make eval-video` makes them):

1. A fresh backend runs the *whole* generation stack: the auction, the creative
   studio with AI copy (replayed Claude latency, evals.pipeline), voice-overs (the
   real ElevenLabs adapter against the sandbox server), the installed bundle loops
   (`make bundle-loops-local`) and production swap timing (D5). No credits.
2. The production web player (vite build) runs in Chromium at 1280×720, exactly
   as the screen does, and reports playback itself (so billing runs too).
3. The agent's recording of the real clip streams in real time, like a camera.
4. Every second the player is captured beside the clip's face-blurred keyframe at
   that instant, with a caption of what the system understood and decided.
5. Each capture is checked: never blank, an approved price that matches the party
   the copy names, no must-never brand, the brand fits the scene, the copy's
   group size matches the people looking, the dwell stage moves forward.

Output: evals/results/video/side-by-side/index.html (+ one JPEG per capture).
Frames are face-blurred review artifacts, gitignored, never committed.
"""

from __future__ import annotations

import argparse
import html
import json
import queue
import re
import shutil
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageStat
from websockets.exceptions import ConnectionClosed

from app.bootstrap.demo import DEMO_SCREEN
from app.contract.messages import (
    AudienceSnapshot,
    ConsoleAuction,
    ConsoleMoment,
    ConsoleReceipt,
    CreativeReady,
    CreativeUpgraded,
)
from app.modules.creative.infrastructure.elevenlabs_tts import ElevenLabsVoiceSynthesizer
from evals.browser import VIEWPORT, build_web, preview
from evals.demo_video import _media_with_loops
from evals.live import CONSOLE_MESSAGES, PLAYER_MESSAGES, live_backend
from evals.pipeline import (
    LatencyFaithfulCopywriter,
    TimedVoice,
    recorded_copy_latencies_ms,
    sandbox_tts,
)
from evals.video.fetch import load_clips
from evals.video.run import OUT as VIDEO_OUT

OUT = VIDEO_OUT / "side-by-side"
CAPTURE_EVERY_S = 1.0
PANE_W, PANE_H = 640, 360
CAPTION_H = 150
NUMBER_WORDS = {"dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6}


@dataclass
class Capture:
    t_s: float
    creative_id: str | None
    image: str
    brands: tuple[str, ...]
    paid: bool
    stage_text: str
    offer: str | None
    price: str | None
    party: int | None
    video: str | None
    voice: bool
    copy_source: str | None
    people: int | None
    looking: int | None
    segments: tuple[str, ...]
    held: tuple[str, ...]
    minor: bool
    checks: dict[str, bool] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


@dataclass
class ClipRun:
    clip: dict[str, Any]
    captures: list[Capture]
    receipts: list[ConsoleReceipt]
    errors: list[str]
    events: list[tuple[float, str, dict[str, Any]]] = field(default_factory=list)


def _spec_facts(message: CreativeReady | CreativeUpgraded) -> dict[str, Any]:
    spec = message.spec
    return {
        "creative_id": message.creative_id,
        "paid": message.auction_id is not None,
        "brands": sorted(s.brand_name for s in spec.slots),
        "party": spec.offer.party_size if spec.offer else None,
        "teaser": spec.stages[0].copy_text if spec.stages else "",
        "copy_source": spec.copy_source.value if spec.copy_source else None,
        "voice": spec.voiceover_url is not None,
    }


class _Listener:
    """Console + player sockets read on threads; keeps the latest of each fact."""

    def __init__(self, backend: Any) -> None:
        self.moment: ConsoleMoment | None = None
        self.auction: ConsoleAuction | None = None
        self.on_air: CreativeReady | None = None
        self.receipts: list[ConsoleReceipt] = []
        # (seconds since t0, kind, facts): what the backend did, independent of the frames.
        self.events: list[tuple[float, str, dict[str, Any]]] = []
        self.t0 = time.perf_counter()
        self._socks = [backend.console(), backend.player(DEMO_SCREEN)]
        self._conns = [s.__enter__() for s in self._socks]
        self._threads = [
            threading.Thread(target=self._read, args=(c, a), daemon=True)
            for c, a in zip(self._conns, (CONSOLE_MESSAGES, PLAYER_MESSAGES), strict=True)
        ]
        for t in self._threads:
            t.start()

    def _read(self, conn: Any, adapter: Any) -> None:
        try:
            for raw in conn:
                message = adapter.validate_json(raw)
                now = time.perf_counter() - self.t0
                if isinstance(message, ConsoleMoment):
                    self.moment = message
                    self.events.append(
                        (
                            now,
                            "moment",
                            {
                                "people": message.people,
                                "looking": message.looking,
                                "sources": sorted(
                                    {src.value for seg in message.segments for src in seg.sources}
                                ),
                            },
                        )
                    )
                elif isinstance(message, ConsoleAuction):
                    self.auction = message
                    self.events.append(
                        (
                            now,
                            "auction",
                            {
                                "swap": message.swap,
                                "trigger": message.trigger,
                                "winners": [w.brand_name for w in message.winners],
                            },
                        )
                    )
                elif isinstance(message, ConsoleReceipt):
                    self.receipts.append(message)
                elif isinstance(message, CreativeReady) and adapter is PLAYER_MESSAGES:
                    self.on_air = message
                    self.events.append((now, "ready", _spec_facts(message)))
                elif isinstance(message, CreativeUpgraded) and adapter is PLAYER_MESSAGES:
                    facts = _spec_facts(message)
                    facts["upgrade"] = message.upgrade_kind.value
                    self.events.append((now, "upgraded", facts))
                    if self.on_air and message.creative_id == self.on_air.creative_id:
                        self.on_air = CreativeReady(
                            screen_id=message.screen_id,
                            creative_id=message.creative_id,
                            auction_id=message.auction_id,
                            spec=message.spec,
                            trace=message.trace,
                        )
        except ConnectionClosed, OSError:
            return

    def close(self) -> None:
        for s in self._socks:
            s.__exit__(None, None, None)


def _stream(backend: Any, lines: list[dict[str, Any]], errors: queue.Queue[str]) -> None:
    """Send the recording like the camera would: real time, fresh timestamps."""
    try:
        with backend.agent() as agent:
            start = time.perf_counter()
            for seq, line in enumerate(lines, start=1):
                due = start + float(line["t_s"])
                if (delay := due - time.perf_counter()) > 0:
                    time.sleep(delay)
                original = AudienceSnapshot.model_validate(line["snapshot"])
                shift = datetime.now(UTC) - (original.published_at or original.measured_at)
                stamps = {
                    k: (v + shift if v is not None else None)
                    for k in ("measured_at", "candidate_at", "detected_at", "published_at")
                    for v in (getattr(original, k),)
                }
                snap = original.model_copy(update={"screen_id": DEMO_SCREEN, "seq": seq, **stamps})
                agent.send(snap.model_dump_json())
    except Exception as exc:  # surfaced in the report, never swallowed
        errors.put(f"stream: {exc!r}")


def _keyframe_at(folder: Path, t_s: float) -> Path | None:
    frames = sorted(folder.glob("*.jpg"))
    best = None
    for frame in frames:
        if float(frame.stem) <= t_s + 0.05:
            best = frame
    return best or (frames[0] if frames else None)


def _font(size: int) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    for name in ("DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _compose(left: Path | None, right: Path, caption: list[str], out: Path) -> None:
    canvas = Image.new("RGB", (PANE_W * 2 + 8, PANE_H + CAPTION_H), (18, 18, 20))
    if left is not None:
        canvas.paste(Image.open(left).convert("RGB").resize((PANE_W, PANE_H)), (0, 0))
    canvas.paste(Image.open(right).convert("RGB").resize((PANE_W, PANE_H)), (PANE_W + 8, 0))
    draw = ImageDraw.Draw(canvas)
    font = _font(15)
    for i, line in enumerate(caption):
        draw.text((10, PANE_H + 8 + i * 20), line[:170], fill=(235, 235, 235), font=font)
    canvas.save(out, quality=82)


def _party_in_copy(text: str) -> int | None:
    m = re.search(r"para los (\d+)", text) or re.search(
        r"\b(?:los|para) (dos|tres|cuatro|cinco|seis)\b", text
    )
    if not m:
        return None
    raw = m.group(1)
    return int(raw) if raw.isdigit() else NUMBER_WORDS.get(raw)


def _check(capture: Capture, shot: Path, clip: dict[str, Any], previous: Capture | None) -> None:
    expect = clip["expect"]
    stat = ImageStat.Stat(Image.open(shot).convert("L"))
    capture.checks["never blank"] = stat.stddev[0] > 6
    capture.checks["no must-never brand"] = not set(capture.brands) & set(expect["never_brands"])
    if capture.paid:
        capture.checks["brand fits the scene"] = bool(
            set(capture.brands) & set(expect["sensible_brands"])
        )
        capture.checks["approved price on the offer"] = capture.price is not None
        named = _party_in_copy(capture.stage_text + " " + (capture.offer or ""))
        if named is not None and capture.looking:
            ok = named <= max(capture.looking, capture.people or 0)
            capture.checks["copy's group ≤ people present"] = ok
            if not ok:
                capture.notes.append(f"copy says {named}, {capture.looking} looking")
        capture.checks["has a video loop"] = capture.video is not None
        if capture.held:
            capture.checks["held product in the pitch"] = any(
                h.split(":")[-1].replace("-", " ") in capture.stage_text.lower()
                or "le falta" in capture.stage_text
                for h in capture.held
            )
    if previous is not None and capture.paid and previous.creative_id == capture.creative_id:
        order = ["teaser", "offer", "close"]
        capture.checks["stage never goes backwards"] = order.index(
            _stage_name(capture)
        ) >= order.index(_stage_name(previous))


def _stage_name(capture: Capture) -> str:
    return (
        capture.notes[0]
        if capture.notes and capture.notes[0] in ("teaser", "offer", "close")
        else "teaser"
    )


def run_clip(clip: dict[str, Any], page: Any, backend: Any) -> ClipRun:
    recording = VIDEO_OUT / clip["id"] / "recording.agent.jsonl"
    keyframes = VIDEO_OUT / clip["id"] / "keyframes.agent"
    lines = [json.loads(x) for x in recording.read_text().splitlines() if x.strip()]
    out = OUT / clip["id"]
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    listener = _Listener(backend)
    errors: queue.Queue[str] = queue.Queue()
    page.goto(page.url)  # fresh player per clip: starts on the house ad
    page.wait_for_timeout(600)
    streamer = threading.Thread(target=_stream, args=(backend, lines, errors), daemon=True)
    started = time.perf_counter()
    listener.t0 = started
    streamer.start()
    captures: list[Capture] = []
    end_s = float(lines[-1]["t_s"]) + 1.0
    while (t := time.perf_counter() - started) <= end_s:
        shot = out / f"player-{t:05.1f}.png"
        page.screenshot(path=str(shot))
        on_air, moment = listener.on_air, listener.moment
        spec = on_air.spec if on_air else None
        paid = bool(on_air and on_air.auction_id)
        stage = page.evaluate(
            "() => [...document.querySelectorAll('.creative-hook, .creative-offer-line,"
            " .creative-close-line')].map(e => e.textContent.trim()).filter(Boolean).join(' · ')"
        )
        stage_name = page.evaluate(
            "() => document.querySelector('.creative-close, .creative-qr') ? 'close' :"
            " (document.querySelector('.creative-price-figure, .creative-offer-line') ? 'offer'"
            " : 'teaser')"
        )
        capture = Capture(
            t_s=round(t, 1),
            creative_id=on_air.creative_id if on_air else None,
            image="",
            brands=tuple(sorted(s.brand_name for s in spec.slots)) if spec and paid else (),
            paid=paid,
            stage_text=str(stage),
            offer=spec.offer.label if spec and spec.offer else None,
            price=spec.offer.price.amount if spec and spec.offer else None,
            party=spec.offer.party_size if spec and spec.offer else None,
            video=spec.video_url.rsplit("/", 1)[-1] if spec and spec.video_url else None,
            voice=bool(spec and spec.voiceover_url),
            copy_source=spec.copy_source.value if spec and spec.copy_source else None,
            people=moment.people if moment else None,
            looking=moment.looking if moment else None,
            segments=tuple(s.label for s in moment.segments) if moment else (),
            held=tuple(h.category for h in moment.held_products) if moment else (),
            minor=bool(moment and moment.minor_likely.value),
            notes=[str(stage_name)],
        )
        _check(capture, shot, clip, captures[-1] if captures else None)
        caption = [
            f"{clip['id']} · t={capture.t_s:4.1f}s · camera: {capture.people} people, "
            f"{capture.looking} looking{' · MINOR' if capture.minor else ''}"
            f"{' · held ' + ','.join(capture.held) if capture.held else ''}",
            f"understood: {', '.join(capture.segments) or '—'}",
            f"screen: {' + '.join(capture.brands) or 'house ad'} · {stage_name} · "
            f"{capture.offer or ''} {('$' + capture.price) if capture.price else ''} · "
            f"copy={capture.copy_source or '-'} · voice={'✓' if capture.voice else '—'} · "
            f"video={capture.video or '—'}",
            f"“{capture.stage_text[:110]}”",
            "checks: "
            + (
                "  ".join(f"{'✓' if ok else '✗'} {name}" for name, ok in capture.checks.items())
                or "—"
            ),
            "expect: " + clip["expect"]["note"][:150],
        ]
        image = out / f"{len(captures):02d}.jpg"
        _compose(_keyframe_at(keyframes, capture.t_s), shot, caption, image)
        shot.unlink()
        capture.image = str(image.relative_to(OUT))
        captures.append(capture)
        time.sleep(max(0.0, CAPTURE_EVERY_S - (time.perf_counter() - started - t)))
    streamer.join(timeout=5)
    time.sleep(4.0)  # D5.5 grace runs out; the last play is reported and billed
    listener.close()
    (out / "events.json").write_text(
        json.dumps(
            [{"t_s": round(t, 2), "kind": k, **f} for t, k, f in listener.events],
            indent=1,
            ensure_ascii=False,
        )
    )
    return ClipRun(clip, captures, list(listener.receipts), list(errors.queue), listener.events)


VOICE_GATE_S = 1.5  # AI copy + its voice-over on air within this of an ad going up
GROUP_STEADY_S = 4.0  # a group steady this long must see its own offer tier
MAX_TIER = 4  # the largest approved party size in the seed
ACTIVITY_CLAIM = re.compile(r"\b(entren\w*|corri\w*|jugaron|jugaste)\b", re.IGNORECASE)
ACTIVITY_EVIDENCE = {"speech", "vlm", "sound"}


@dataclass
class TimelineCheck:
    name: str
    gate: bool  # False = tracked only (needs a team decision before it may fail a build)
    failures: list[str]


def timeline_checks(events: list[tuple[float, str, dict[str, Any]]]) -> list[TimelineCheck]:
    """Checks on what the backend did over time, independent of capture timing."""
    creatives = [(t, f) for t, k, f in events if k in ("ready", "upgraded")]
    moments = [(t, f) for t, k, f in events if k == "moment"]

    voice: list[str] = []
    readies = [(t, f) for t, k, f in events if k == "ready" and f["paid"]]
    for index, (t0, ready) in enumerate(readies):
        replaced_at = readies[index + 1][0] if index + 1 < len(readies) else float("inf")
        complete = next(
            (
                t
                for t, f in creatives
                if f["creative_id"] == ready["creative_id"]
                and t >= t0
                and f["voice"]
                and f["copy_source"] in ("ai", "cache")
            ),
            None,
        )
        if replaced_at - t0 < VOICE_GATE_S:
            continue  # replaced before the gate: nothing to hold it to
        if complete is None or complete - t0 > VOICE_GATE_S:
            took = "never" if complete is None else f"{complete - t0:.2f}s"
            voice.append(f"t={t0:.1f}s {'+'.join(ready['brands'])}: copy+voice {took}")

    def party_on_air(at: float) -> int | None:
        current = None
        for t, f in creatives:
            if t > at:
                break
            current = f["party"] if f["paid"] else None
        return current

    group: list[str] = []
    for index, (t0, moment) in enumerate(moments):
        looking = moment["looking"]
        end = moments[index + 1][0] if index + 1 < len(moments) else t0
        if looking < 1 or end - t0 < GROUP_STEADY_S:
            continue
        on_air = party_on_air(t0 + GROUP_STEADY_S)
        if on_air is not None and on_air != min(looking, MAX_TIER):
            group.append(f"t={t0:.1f}–{end:.1f}s: {looking} looking, offer for {on_air}")

    claims: list[str] = []
    for t0, f in creatives:
        if not f["paid"] or not ACTIVITY_CLAIM.search(f["teaser"]):
            continue
        evidence: dict[str, Any] = next(
            (m for t, m in reversed(moments) if t <= t0), {"sources": []}
        )
        if not ACTIVITY_EVIDENCE & set(evidence["sources"]):
            claims.append(f"t={t0:.1f}s “{f['teaser']}” from {evidence['sources'] or 'nothing'}")
    return [
        TimelineCheck("AI copy + voice-over within 1.5 s", True, voice),
        TimelineCheck("a steady group sees its own offer tier", True, group),
        TimelineCheck("activity claims need speech/VLM/sound evidence", False, sorted(set(claims))),
    ]


def render(runs: list[ClipRun]) -> str:
    total = sum(len(c.checks) for r in runs for c in r.captures)
    failed = [
        (r.clip["id"], c.t_s, name)
        for r in runs
        for c in r.captures
        for name, ok in c.checks.items()
        if not ok
    ]
    parts = [
        "<!doctype html><meta charset=utf-8><title>Video ↔ ad</title>",
        "<style>body{font:14px system-ui;background:#111;color:#eee;margin:24px}"
        "img{width:100%;max-width:1288px;display:block;margin:6px 0 18px}"
        "h2{margin-top:40px}.bad{color:#ff8a80}.ok{color:#9be89b}</style>",
        "<h1>Real footage ↔ the ad it produced</h1>",
        f"<p>{len(runs)} clips · {total - len(failed)}/{total} capture checks pass</p>",
    ]
    if failed:
        parts.append("<ul class=bad>")
        parts += [f"<li>{html.escape(c)} t={t}s: {html.escape(n)}</li>" for c, t, n in failed]
        parts.append("</ul>")
    for run in runs:
        clip = run.clip
        billed = sum(Decimal(r.total.amount) for r in run.receipts)
        parts.append(
            f"<h2>{html.escape(clip['id'])}</h2><p>{html.escape(clip['scene'])}<br>"
            f"Expect: {html.escape(clip['expect']['note'])}<br>"
            f"Receipts: {len(run.receipts)} · billed ${billed} · "
            f"source: <a href='{html.escape(clip['page'])}'>{html.escape(clip['title'])}</a> "
            f"({html.escape(clip['artist'])}, {html.escape(clip['license'])})</p>"
        )
        parts += [f"<p class=bad>{html.escape(e)}</p>" for e in run.errors]
        for check in timeline_checks(run.events):
            css = "ok" if not check.failures else ("bad" if check.gate else "")
            mark = "✓" if not check.failures else "✗"
            kind = "" if check.gate else " (tracked)"
            detail = "; ".join(check.failures[:4])
            parts.append(
                f"<p class={css or 'ok'}>{mark} {html.escape(check.name)}{kind}"
                f"{': ' + html.escape(detail) if detail else ''}</p>"
            )
        parts += [f"<img src='{html.escape(c.image)}' loading=lazy>" for c in run.captures]
    return "\n".join(parts)


def main(argv: list[str] | None = None) -> int:
    from playwright.sync_api import sync_playwright

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", action="append")
    parser.add_argument("--reuse-build", action="store_true")
    args = parser.parse_args(argv)
    clips = [
        c
        for c in load_clips()
        if (not args.only or c["id"] in args.only)
        and (VIDEO_OUT / c["id"] / "recording.agent.jsonl").exists()
    ]
    build_web(reuse=args.reuse_build)
    OUT.mkdir(parents=True, exist_ok=True)
    runs: list[ClipRun] = []
    copywriter = LatencyFaithfulCopywriter(recorded_copy_latencies_ms(), scale=1.0, seed=18)
    with sandbox_tts() as (base_url, key, voice_id), tempfile.TemporaryDirectory() as tmp:
        voice = TimedVoice(
            ElevenLabsVoiceSynthesizer(key, base_url=base_url), latency_ms=350.0, seed=18
        )
        for clip in clips:
            print(f"== {clip['id']}", file=sys.stderr)
            # A fresh market per clip: budgets, caches and on-air state never leak between scenes.
            with (
                live_backend(
                    media_dir=_media_with_loops(Path(tmp) / clip["id"]),
                    copywriter=copywriter,
                    voice_synthesizer=voice,
                    creative_ai_copy=True,
                    elevenlabs_narrator_voice_id=voice_id,
                    elevenlabs_char_budget=100_000,
                    auction_swap_holds=True,
                ) as backend,
                preview(backend.http_url) as web,
                sync_playwright() as pw,
            ):
                browser = pw.chromium.launch(
                    channel="chromium", args=["--autoplay-policy=no-user-gesture-required"]
                )
                page = browser.new_page(viewport=VIEWPORT)
                page.goto(f"{web}/screen?id={DEMO_SCREEN}")
                page.wait_for_timeout(800)
                runs.append(run_clip(clip, page, backend))
                browser.close()
    (OUT / "index.html").write_text(render(runs), encoding="utf-8")
    summary = [
        {
            "clip": r.clip["id"],
            "captures": len(r.captures),
            "failed": [(c.t_s, n) for c in r.captures for n, ok in c.checks.items() if not ok],
            "billed": str(sum(Decimal(x.total.amount) for x in r.receipts)),
            "errors": r.errors,
            "timeline": {
                c.name: {"gate": c.gate, "failures": c.failures} for c in timeline_checks(r.events)
            },
        }
        for r in runs
    ]
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    for row in summary:
        print(
            f"{row['clip']:26} captures={row['captures']:2} billed=${row['billed']:>5} "
            f"failed={row['failed'][:4]}"
        )
        for name, check in row["timeline"].items():
            if check["failures"]:
                kind = "GATE" if check["gate"] else "tracked"
                print(f"    {kind} {name}: {check['failures'][:3]}")
    print(f"→ {OUT / 'index.html'}")
    gate_failed = any(
        check["gate"] and check["failures"] for r in summary for check in r["timeline"].values()
    )
    return 0 if not gate_failed and not any(r["failed"] or r["errors"] for r in summary) else 1


if __name__ == "__main__":
    raise SystemExit(main())
