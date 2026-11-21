"""Live object-score probe for the counter check: `python -m agent.vision.probe`.

Point the webcam at the counter, hold each demo prop where the judges will, and
watch the table: every vocabulary label seen in the last second, its best
score, and whether the agent would publish it (score ≥ the floor *and* near a
tracked person). Run it once per venue, before rehearsal: lighting moves the
scores more than anything else.

It runs the exact production detector: the local `UltralyticsDetector`, or the
remote GPU service with `--gpu-url` (which also proves the service's YOLOE
weights and TensorRT engine answer with the shared vocabulary).

Privacy (CLAUDE.md invariant 3): frames live only in memory and are dropped
after detection. Nothing is written or sent anywhere except, with
`--gpu-url`, to our own GPU service over the same WSS the agent uses. Only
object labels and scores are printed; nothing about any person.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from collections import deque
from datetime import datetime, timedelta

from agent.vision.detector import Detector
from agent.vision.types import FrameDetections
from agent.vision.vocabulary import DEFAULT_OBJECT_MODEL, OBJECT_CONF, OBJECT_LABELS

PERSON_PAD = 0.15  # same reach as SnapshotConfig.object_person_pad


def summarize(
    window: deque[FrameDetections], *, floor: float = OBJECT_CONF
) -> list[tuple[str, float, bool, bool]]:
    """(label, best score, above the floor, near a person) for every label in the window."""
    best: dict[str, tuple[float, bool]] = {}
    for frame in window:
        reach = [p.box.expanded(PERSON_PAD) for p in frame.persons]
        for obj in frame.objects:
            near = any(area.iou(obj.box) > 0 or area.contains(obj.box.center) for area in reach)
            score, was_near = best.get(obj.label, (0.0, False))
            best[obj.label] = (max(score, obj.score), was_near or near)
    return [
        (label, score, score >= floor, near)
        for label, (score, near) in sorted(best.items(), key=lambda kv: -kv[1][0])
    ]


def render(rows: list[tuple[str, float, bool, bool]], *, people: int, ms: float, name: str) -> str:
    lines = [f"{name} · {people} people · {ms:.0f} ms/frame · floor {OBJECT_CONF:.2f}", ""]
    if not rows:
        lines.append("  (no vocabulary object in view)")
    for label, score, above, near in rows:
        verdict = "PUBLISHED" if above and near else ("no person near" if above else "below floor")
        lines.append(f"  {label:<18} {score:4.2f}  {'█' * round(score * 20):<20}  {verdict}")
    unknown = sorted({label for label, *_ in rows} - OBJECT_LABELS)
    if unknown:
        lines.append(f"\n  not in the vocabulary (ignored by the agent): {', '.join(unknown)}")
    return "\n".join(lines)


def _detector(args: argparse.Namespace) -> Detector:
    every = timedelta(milliseconds=200)  # the agent's live object cadence
    if args.gpu_url:
        from agent.vision.remote import RemoteGpuDetector

        remote = RemoteGpuDetector(
            args.gpu_url,
            token=os.environ.get("GPU_TOKEN") or None,
            object_every=1,
            object_interval=every,
        )
        remote.warmup()
        return remote
    from agent.vision.adapters import UltralyticsDetector

    local = UltralyticsDetector(
        object_model=args.object_model, object_interval=every, device=args.device
    )
    local.warmup()
    return local


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--camera", type=int, default=0)
    parser.add_argument("--object-model", default=DEFAULT_OBJECT_MODEL)
    parser.add_argument("--device", default=None, help="e.g. 0, cpu, mps")
    parser.add_argument("--gpu-url", help="probe the remote GPU service instead (wss://…/infer)")
    parser.add_argument("--seconds", type=float, default=0, help="stop after N s (0 = Ctrl-C)")
    parser.add_argument("--once", action="store_true", help="print one summary at the end only")
    args = parser.parse_args(argv)

    from agent.vision.capture import LatestFrameCamera

    detector = _detector(args)
    camera = LatestFrameCamera(args.camera)
    window: deque[FrameDetections] = deque()
    serial, started, ms = 0, time.monotonic(), 0.0
    try:
        while not args.seconds or time.monotonic() - started < args.seconds:
            latest = camera.latest(serial)
            if latest is None:
                time.sleep(0.005)
                continue
            frame, measured_at, serial = latest
            t = time.perf_counter()
            window.append(detector.detect(frame, measured_at))
            del frame  # never kept past detection
            ms = (
                0.8 * ms + 0.2 * (time.perf_counter() - t) * 1000
                if ms
                else (time.perf_counter() - t) * 1000
            )
            cutoff: datetime = measured_at - timedelta(seconds=1)
            while window and window[0].measured_at < cutoff:
                window.popleft()
            if not args.once:
                text = render(
                    summarize(window), people=len(window[-1].persons), ms=ms, name=detector.name
                )
                sys.stdout.write("\x1b[H\x1b[J" + text + "\n")
                sys.stdout.flush()
    except KeyboardInterrupt:
        pass
    finally:
        camera.close()
    if window:
        print(render(summarize(window), people=len(window[-1].persons), ms=ms, name=detector.name))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
