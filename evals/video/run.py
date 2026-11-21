"""Real stock video -> real perception -> live backend -> "does this ad make sense?" report.

    uv run python -m evals.video.fetch          # once: download the CC clips
    uv run python -m evals.video.run            # all clips (make eval-video)
    uv run python -m evals.video.run --only coffee-counter --reuse

For every clip in clips.json:

1. evals/video/perceive.py runs YOLO26 pose + objects on the clip (CPU, its
   own uv environment) and writes the agent's snapshot JSONL plus blurred
   keyframes.
2. The recording is replayed in real time through a fresh live backend
   (harness.replay_recording): debounce, holds and swaps behave as live.
3. The screen timeline is scored against the clip's human expectation
   (`sensible_brands`, `never_brands`), and the HTML review report shows
   each change next to the blurred frame that caused it.

Output: evals/results/video/index.html (+ scorecard in evals/results/).
"""

from __future__ import annotations

import argparse
import html
import itertools
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from evals import metrics as m, scorecard as sc
from evals.harness import RunRecord, replay_recording
from evals.video.fetch import clip_path, load_clips

HERE = Path(__file__).resolve().parent
OUT = sc.RESULTS_DIR / "video"
PERCEIVE = HERE / "perceive.py"
UNMAPPED_MIN_SHARE = 0.3  # an object seen this often with no segment is a catalog gap


@dataclass(slots=True)
class Interval:
    start_s: float
    end_s: float
    brands: frozenset[str]
    copy: str
    layout: str
    step: int | None


@dataclass(slots=True)
class ClipResult:
    clip: dict[str, Any]
    meta: dict[str, Any]
    run: RunRecord
    timeline: list[Interval]
    lines: list[dict[str, Any]]
    metrics: dict[str, Any] = field(default_factory=dict)
    objects_seen: dict[str, float] = field(default_factory=dict)
    segments_seen: dict[str, float] = field(default_factory=dict)
    unmapped: list[str] = field(default_factory=list)
    keyframes: Path | None = None


EDGE = HERE.parents[1] / "edge"


def perceive(clip: dict[str, Any], *, reuse: bool, perceiver: str) -> Path:
    """Run perception on the clip; returns the snapshot recording (docs/evals.md §4).

    ``agent`` runs the production perception agent (edge/agent/vision, record
    mode). ``standin`` runs evals/video/perceive.py, the eval-only stand-in.
    Either way, blurred review keyframes come from the stand-in at the moments
    the recording's facts change.
    """
    folder = OUT / clip["id"]
    recording = folder / f"recording.{perceiver}.jsonl"
    video = clip_path(clip)
    if not video.exists():
        raise SystemExit(f"missing {video}: run `uv run python -m evals.video.fetch` first")
    keyframes = folder / f"keyframes.{perceiver}"
    if reuse and recording.exists() and any(keyframes.glob("*.jpg")):
        return recording
    keyframes.mkdir(parents=True, exist_ok=True)
    for stale in keyframes.glob("*.jpg"):  # our own generated frames from a previous run
        stale.unlink()
    env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
    window = ["--start", str(clip["start_s"]), "--duration", str(clip["duration_s"])]
    standin = ["uv", "run", "--script", str(PERCEIVE), str(video), *window]
    if perceiver == "agent":
        subprocess.run(
            [
                "uv",
                "run",
                "python",
                "-m",
                "agent.vision",
                "--screen",
                "replay",
                "--video",
                str(video),
                "--record",
                str(recording),
                "--offline",
                "--sample-fps",
                "5",
                "--device",
                "cpu",
                *window,
            ],
            check=True,
            cwd=EDGE,
            env=env,
        )
        subprocess.run(
            [*standin, "--keyframes-for", str(recording), "--keyframes", str(keyframes)],
            check=True,
            cwd=OUT,
            env=env,
        )
    else:
        subprocess.run(
            [*standin, "--out", str(recording), "--keyframes", str(keyframes)],
            check=True,
            cwd=OUT,
            env=env,  # model weights are cached here (gitignored)
        )
    return recording


def score(clip: dict[str, Any], recording: Path, run: RunRecord) -> ClipResult:
    lines = [json.loads(x) for x in recording.read_text(encoding="utf-8").splitlines() if x.strip()]
    meta_path = recording.with_suffix(".meta.json")
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    end = lines[-1]["t_s"] + 1 / float(meta.get("sample_fps", 5)) if lines else 0.0
    initial = run.initial
    timeline: list[Interval] = []
    current = Interval(0.0, end, frozenset(), _copy(initial) if initial else "", "house", None)
    for decision in run.decisions:
        if not decision.swapped:
            continue
        t = float(lines[decision.step]["t_s"])
        if decision.brands == current.brands and _copy(decision.creative) == current.copy:
            continue
        current.end_s = t
        if current.end_s > current.start_s:
            timeline.append(current)
        current = Interval(
            t,
            end,
            decision.brands,
            _copy(decision.creative),
            decision.creative.spec.layout_id,
            decision.step,
        )
    timeline.append(current)

    expect = clip.get("expect", {})
    sensible = frozenset(expect.get("sensible_brands", []))
    never = frozenset(expect.get("never_brands", []))
    total = max(end, 1e-9)
    paid = sum(i.end_s - i.start_s for i in timeline if i.brands)
    good = sum(i.end_s - i.start_s for i in timeline if i.brands and i.brands <= sensible)
    bad = sum(i.end_s - i.start_s for i in timeline if i.brands & never)

    objects: dict[str, int] = {}
    for line in lines:
        for obj in line["snapshot"]["objects"]:
            objects[obj["label"]] = objects.get(obj["label"], 0) + 1
    objects_seen = {k: v / len(lines) for k, v in sorted(objects.items(), key=lambda kv: -kv[1])}
    segments: dict[str, int] = {}
    for moment in run.moments.values():
        for segment in moment.segments:
            segments[segment.label] = segments.get(segment.label, 0) + 1
    moments = max(len(run.moments), 1)
    segments_seen = {k: v / moments for k, v in segments.items()}
    unmapped = [
        label
        for label, share in objects_seen.items()
        if share >= UNMAPPED_MIN_SHARE and not segments
    ]
    decisions = list(run.decisions)
    result = ClipResult(
        clip=clip,
        meta=meta,
        run=run,
        timeline=timeline,
        lines=lines,
        objects_seen=objects_seen,
        segments_seen=segments_seen,
        unmapped=unmapped,
    )
    result.metrics = {
        "duration_s": round(total, 2),
        "paid_share": paid / total,
        "ad_precision": good / paid if paid else None,
        "sensible_ad_reach": good / total if sensible else None,
        "inappropriate_ad_seconds": round(bad, 2),
        "screen_changes": len(timeline) - 1,
        "flicker_per_minute": (len(timeline) - 1) * 60 / total,
        "raw_fact_changes": _changes([x["raw"] for x in lines if "raw" in x])
        if any("raw" in x for x in lines)
        else meta.get("raw_fact_changes"),
        "published_fact_changes": _changes([x["snapshot"] for x in lines]),
        "cpu_infer_p50_ms": meta.get("cpu_infer_p50_ms", meta.get("infer_p50_ms")),
        "cpu_infer_p95_ms": meta.get("infer_p95_ms"),
        **_house_time(lines, timeline, run, end),
        "invariant_violations": len(m.invariant_violations(decisions)),
        **{
            k: v
            for k, v in m.latency_kpis(decisions).items()
            if k.startswith("server_decision_p95")
        },
    }
    return result


def _house_time(
    lines: list[dict[str, Any]], timeline: list[Interval], run: RunRecord, end: float
) -> dict[str, float]:
    """Attribute house time using source intervals and the latest observed decision."""
    seconds = dict.fromkeys(
        ("no_attention", "no_target_signal", "auction_no_fill", "winner_not_on_air", "unobserved"),
        0.0,
    )
    auctions = {d.step: d.auction for d in run.decisions if d.auction is not None}
    if lines:
        seconds["unobserved"] = float(lines[0]["t_s"])
    moment = auction = None
    interval = 0
    for step, line in enumerate(lines):
        moment = run.moments.get(step, moment)
        auction = auctions.get(step, auction)
        start = float(line["t_s"])
        until = float(lines[step + 1]["t_s"]) if step + 1 < len(lines) else end
        while interval + 1 < len(timeline) and timeline[interval].end_s <= start:
            interval += 1
        if timeline[interval].brands:
            continue
        if not line["snapshot"]["looking"]:
            reason = "no_attention"
        elif moment is None:
            reason = "unobserved"
        elif not moment.segments and not moment.held_products:
            reason = "no_target_signal"
        elif auction is None or auction.moment_id != moment.moment_id:
            reason = "unobserved"
        elif not auction.winners:
            reason = "auction_no_fill"
        else:
            reason = "winner_not_on_air"
        seconds[reason] += until - start
    return {f"house_{reason}_s": round(value, 2) for reason, value in seconds.items()}


def _changes(facts: list[dict[str, Any]]) -> int:
    def key(f: dict[str, Any]) -> tuple[Any, ...]:
        objects = f.get("objects", [])
        labels = (
            sorted(o["label"] for o in objects) if isinstance(objects, list) else sorted(objects)
        )
        return (f.get("people"), f.get("looking"), tuple(labels))

    keys = [key(f) for f in facts]
    return sum(a != b for a, b in itertools.pairwise(keys))


def _copy(creative: Any) -> str:
    return " / ".join(slot.copy_text for slot in creative.spec.slots)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--only", action="append", help="clip id (repeatable)")
    parser.add_argument(
        "--reuse", action="store_true", help="reuse recordings newer than perceive.py"
    )
    parser.add_argument("--speed", type=float, default=1.0, help="replay speed (1 = real time)")
    parser.add_argument(
        "--perceiver",
        choices=["agent", "standin"],
        default="agent",
        help="agent = production edge/agent/vision (needs `cd edge && uv sync --extra vision`)",
    )
    args = parser.parse_args(argv)
    clips = [c for c in load_clips() if not args.only or c["id"] in args.only]
    OUT.mkdir(parents=True, exist_ok=True)
    results = []
    for clip in clips:
        print(f"== {clip['id']}", file=sys.stderr)
        recording = perceive(clip, reuse=args.reuse, perceiver=args.perceiver)
        result = score(clip, recording, replay_recording(recording, speed=args.speed))
        result.keyframes = OUT / clip["id"] / f"keyframes.{args.perceiver}"
        results.append(result)
    report = OUT / "index.html"
    report.write_text(render_html(results), encoding="utf-8")

    precisions = [
        r.metrics["ad_precision"] for r in results if r.metrics["ad_precision"] is not None
    ]
    reaches = [
        r.metrics["sensible_ad_reach"]
        for r in results
        if r.metrics["sensible_ad_reach"] is not None
    ]
    summary: dict[str, Any] = {
        "video_clips": len(results),
        "invariant_violations": sum(r.metrics["invariant_violations"] for r in results),
        "inappropriate_ad_seconds": sum(r.metrics["inappropriate_ad_seconds"] for r in results),
        "video_ad_precision": sum(precisions) / len(precisions) if precisions else None,
        "video_sensible_ad_reach": sum(reaches) / len(reaches) if reaches else None,
        "video_paid_share": sum(r.metrics["paid_share"] for r in results) / max(len(results), 1),
        "video_flicker_per_minute": sum(r.metrics["flicker_per_minute"] for r in results)
        / max(len(results), 1),
        "video_clips_with_unmapped_signals": sum(bool(r.unmapped) for r in results),
    }
    verdicts = sc.judge(summary, {})
    sections = [
        {
            "name": r.clip["id"],
            "beat": r.clip.get("scene", ""),
            "checks": 1,
            "checks_failed": int(r.metrics["inappropriate_ad_seconds"] > 0),
            "decisions": len(r.run.decisions),
            "duration_s": r.run.duration_s,
            "errors": [],
        }
        for r in results
    ]
    built = sc.build_report(
        suite="video",
        metrics=summary,
        verdicts=verdicts,
        sections=sections,
        violations=[],
        failures=[],
    )
    markdown = sc.render_markdown(built, sc.load_history(suite="video") + [built])
    sc.save_run(built, markdown)
    print(markdown)
    print(f"\nreview report: {report}", file=sys.stderr)
    return 1 if sc.failed(verdicts) else 0


# ----------------------------------------------------------------------------- HTML

CSS = """
:root{--bg:#f7f7f5;--card:#fff;--ink:#1d1d1b;--muted:#6b6b66;--line:#e4e4df;--good:#1f7a4d;--warn:#a15c00;--bad:#b3261e;--chip:#efefea}
@media (prefers-color-scheme:dark){:root{--bg:#141413;--card:#1e1e1c;--ink:#ecebe6;--muted:#a3a29b;--line:#33332f;--good:#5cc08a;--warn:#e0a34a;--bad:#f2766b;--chip:#2a2a27}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}
main{max-width:1100px;margin:0 auto;padding:24px 16px 64px}h1{font-size:26px;margin:0 0 4px}h2{font-size:19px;margin:0}
.sub{color:var(--muted);margin:0 0 20px}.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px;margin:16px 0}
table{width:100%;border-collapse:collapse;font-size:14px}th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--muted);font-weight:600}td.n{text-align:right;font-variant-numeric:tabular-nums}
.chip{display:inline-block;background:var(--chip);border-radius:999px;padding:1px 9px;margin:2px 4px 2px 0;font-size:13px}
.good{color:var(--good)}.warn{color:var(--warn)}.bad{color:var(--bad)}.muted{color:var(--muted)}
.tl{display:flex;height:22px;border-radius:6px;overflow:hidden;margin:10px 0 4px;border:1px solid var(--line)}
.tl div{height:100%;font-size:11px;white-space:nowrap;overflow:hidden;padding:2px 4px;color:#fff}
.row{display:grid;grid-template-columns:240px 1fr;gap:14px;padding:12px 0;border-top:1px solid var(--line)}
.row img{width:100%;border-radius:8px;display:block}@media (max-width:640px){.row{grid-template-columns:1fr}}
.ad{border-left:4px solid var(--line);padding-left:10px}.ad.paid{border-color:var(--good)}.ad.off{border-color:var(--warn)}.ad.never{border-color:var(--bad)}
.replay{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin:12px 0}
.replay video{width:100%;aspect-ratio:16/9;background:#151515;border-radius:8px}
.replay output{display:block;background:var(--chip);border-radius:8px;padding:16px;min-width:0}
@media (max-width:640px){.replay{grid-template-columns:1fr}}
small{color:var(--muted)}a{color:inherit}
"""
REPLAY_SCRIPT = """
for (const replay of document.querySelectorAll(".replay")) {
    const video = replay.querySelector("video");
    const output = replay.querySelector("output");
    const offset = Number(replay.dataset.offset);
    const duration = Number(replay.dataset.duration);
    const intervals = JSON.parse(replay.dataset.intervals);
    const sync = () => {
        const t = video.currentTime - offset;
        const decision = intervals.find(item => item.start <= t && t < item.end);
        output.querySelector("[data-time]").textContent = t >= 0 && t < duration
            ? t.toFixed(1) + " s into test"
            : "Outside the scored window";
        output.querySelector("strong").textContent = decision?.brands ?? "";
        output.querySelector("p").textContent = decision?.copy ?? "";
    };
    video.addEventListener("loadedmetadata", () => { video.currentTime = offset; sync(); });
    video.addEventListener("play", () => {
        if (video.currentTime < offset || video.currentTime >= offset + duration) {
            video.currentTime = offset;
        }
    });
    video.addEventListener("timeupdate", () => {
        if (!video.paused && video.currentTime >= offset + duration) video.pause();
        sync();
    });
    video.addEventListener("seeked", sync);
    video.addEventListener("error", () => {
        output.querySelector("[data-time]").textContent = "The browser could not load this clip.";
    });
    sync();
}
"""
COLORS = {
    "": "#8a8a84",
    "Voltio": "#5f8f1f",
    "Café Patio": "#a8642a",
    "Malta Sur": "#b38f1c",
    "Rayo": "#6b5fd6",
}


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.0f}%"


def _ms(value: float | None) -> str:
    return "—" if value is None else f"{value:.0f} ms"


def _brands(brands: frozenset[str]) -> str:
    return " + ".join(sorted(brands)) or "house ad"


def _keyframe(result: ClipResult, t: float) -> str | None:
    if result.keyframes is None:
        return None
    best = None
    for frame in sorted(result.keyframes.glob("*.jpg")):
        if float(frame.stem) <= t + 0.6:
            best = frame
    return str(best.relative_to(OUT)) if best else None


def _facts(result: ClipResult, step: int | None) -> str:
    if step is None:
        return "<small>before the first moment</small>"
    snap = result.lines[step]["snapshot"]
    objs = "".join(
        f'<span class="chip">{html.escape(o["label"])} {o["score"]:.2f}</span>'
        for o in snap["objects"]
    )
    moment = result.run.moments.get(step)
    segs = (
        "".join(f'<span class="chip">{html.escape(s.label)}</span>' for s in moment.segments)
        if moment and moment.segments
        else '<span class="warn">no segment matched</span>'
    )
    return (
        f"<div>{snap['people']} near the screen · {snap['looking']} looking</div>"
        f"<div>held/near: {objs or '<small>nothing</small>'}</div><div>understood: {segs}</div>"
    )


def _perceiver(result: ClipResult) -> str:
    meta = result.meta
    models = [str(meta[k]) for k in ("pose_model", "det_model", "object_model") if meta.get(k)]
    who = (
        "edge agent"
        if result.keyframes and result.keyframes.name.endswith("agent")
        else "eval stand-in"
    )
    return f"{who}: {' + '.join(models) or 'YOLO26'} on CPU"


def _video_replay(result: ClipResult) -> str:
    original = clip_path(result.clip)
    preview = original.with_suffix(".preview.webm")
    source_path = preview if preview.is_file() else original
    source = html.escape(Path(os.path.relpath(source_path, OUT)).as_posix())
    intervals = html.escape(
        json.dumps(
            [
                {"start": i.start_s, "end": i.end_s, "brands": _brands(i.brands), "copy": i.copy}
                for i in result.timeline
            ]
        )
    )
    offset = float(result.clip["start_s"])
    duration = float(result.metrics["duration_s"])
    return (
        f'<div class="replay" data-offset="{offset}" data-duration="{duration}" '
        f'data-intervals="{intervals}">'
        f'<video src="{source}" controls muted playsinline preload="none" '
        'aria-label="Source video"></video>'
        "<output><small>Auction replay · <span data-time></span></small><br>"
        "<strong></strong><p></p></output></div>"
    )


def render_html(results: list[ClipResult]) -> str:
    rows = []
    for r in results:
        mt = r.metrics
        prec = mt["ad_precision"]
        cls = (
            "muted"
            if prec is None
            else ("good" if prec >= 0.8 else "warn" if prec >= 0.5 else "bad")
        )
        rows.append(
            f"<tr><td><a href='#{r.clip['id']}'>{html.escape(r.clip['id'])}</a><br><small>{html.escape(r.clip.get('scene', ''))}</small></td>"
            f"<td class='n'>{_pct(mt['paid_share'])}</td><td class='n {cls}'>{_pct(prec)}</td>"
            f"<td class='n'>{_pct(mt['sensible_ad_reach'])}</td>"
            f"<td class='n {'bad' if mt['inappropriate_ad_seconds'] else ''}'>{mt['inappropriate_ad_seconds']:.1f}s</td>"
            f"<td class='n'>{mt['raw_fact_changes']} → {mt['published_fact_changes']}</td>"
            f"<td class='n'>{_ms(mt['cpu_infer_p50_ms'])}</td>"
            f"<td>{''.join(f'<span class=chip>{html.escape(u)}</span>' for u in r.unmapped[:5]) or '<small>—</small>'}</td></tr>"
        )
    cards = []
    for r in results:
        mt = r.metrics
        house = " · ".join(
            f"{label} {mt[key]:.1f}s"
            for key, label in (
                ("house_no_attention_s", "no attention"),
                ("house_no_target_signal_s", "no target signal"),
                ("house_auction_no_fill_s", "auction no fill"),
                ("house_winner_not_on_air_s", "winner pending"),
                ("house_unobserved_s", "unobserved"),
            )
            if mt[key] > 0
        )
        expect = r.clip.get("expect", {})
        sensible = frozenset(expect.get("sensible_brands", []))
        never = frozenset(expect.get("never_brands", []))
        total = max(r.metrics["duration_s"], 1e-9)
        bar = "".join(
            f"<div title='{html.escape(_brands(i.brands))} {i.start_s:.1f}–{i.end_s:.1f}s' style='width:{(i.end_s - i.start_s) / total * 100:.2f}%;background:{COLORS.get(sorted(i.brands)[0] if i.brands else '', '#555')}'>{html.escape(_brands(i.brands))}</div>"
            for i in r.timeline
        )
        changes = []
        for i in r.timeline:
            frame = _keyframe(r, i.start_s)
            kind = (
                "never"
                if i.brands & never
                else ("paid" if i.brands and i.brands <= sensible else ("off" if i.brands else ""))
            )
            verdict = {
                "never": '<b class="bad">✗ must never show here</b>',
                "paid": '<b class="good">✓ makes sense</b>',
                "off": '<b class="warn">? off-target</b>',
                "": '<span class="muted">house ad</span>'
                + (' · <span class="warn">missed opportunity</span>' if sensible else ""),
            }[kind]
            img = (
                f"<img src='{frame}' alt='blurred frame at {i.start_s:.1f}s'>"
                if frame
                else "<small>no frame</small>"
            )
            changes.append(
                f"<div class='row'><div>{img}<small>{i.start_s:.1f}s – {i.end_s:.1f}s</small></div>"
                f"<div><div class='ad {kind}'><b>{html.escape(_brands(i.brands))}</b> · <small>{html.escape(i.layout)}</small><br>"
                f"“{html.escape(i.copy)}”<br>{verdict}</div><div style='margin-top:8px'>{_facts(r, i.step)}</div></div></div>"
            )
        credit = (
            f"<a href='{html.escape(r.clip['page'])}'>{html.escape(r.clip['title'])}</a> · "
            f"{html.escape(r.clip.get('artist') or 'unknown')} · {html.escape(r.clip['license'])}"
        )
        cards.append(
            f"<section class='card' id='{r.clip['id']}'><h2>{html.escape(r.clip['id'])}</h2>"
            f"<p class='sub' style='margin:4px 0'>{html.escape(r.clip.get('scene', ''))}</p>"
            f"<small>Would make sense: {html.escape(', '.join(sorted(sensible)) or 'nothing specific (house ad is fine)')} · "
            f"never: {html.escape(', '.join(sorted(never)) or '—')}"
            f"{' · ' + html.escape(expect['note']) if expect.get('note') else ''}</small>"
            f"{_video_replay(r)}"
            f"<div class='tl'>{bar}</div><small>{r.metrics['duration_s']:.0f}s · {r.metrics['screen_changes']} screen changes · "
            f"{html.escape(_perceiver(r))}</small>"
            f"<p><small>House ad time · {house or 'none'}</small></p>"
            f"{''.join(changes)}<p><small>Source: {credit}. Faces blurred; frames are local review artifacts, never committed.</small></p></section>"
        )
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>Video Ad-Fit Review</title><style>{CSS}</style></head><body><main>"
        "<h1>Does the ad make sense?</h1><p class='sub'>Real stock video → YOLO26 perception → live backend (auction + creative) → what the screen showed, "
        "judged against what a person would expect for that scene.</p>"
        "<section class='card'><table><thead><tr><th>Clip</th><th>Paid time</th><th>Ad precision</th><th>Sensible reach</th>"
        "<th>Must-never</th><th>Facts: raw → published</th><th>CPU / frame</th><th>Seen, not understood</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table><p><small>Ad precision: share of paid screen-time showing a brand that fits the scene. "
        "Sensible reach: share of the whole clip with a fitting ad on air. Seen, not understood: objects perception reported in ≥30% "
        "of samples that produced no audience segment (a catalog gap, not a model gap).</small></p></section>"
        f"{''.join(cards)}</main><script>{REPLAY_SCRIPT}</script></body></html>"
    )


if __name__ == "__main__":
    sys.exit(main())
