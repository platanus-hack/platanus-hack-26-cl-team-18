"""Run vision (from edge/, after `uv sync --extra vision`).

    uv run python -m agent.vision --screen demo-01 --markers props/markers.json   # webcam
    uv run python -m agent.vision --screen demo-01 --video run.mp4                # replay, paced
    uv run python -m agent.vision --screen replay --video clip.mp4 \\
        --record evals/perception/recordings/clip.jsonl --offline                 # eval recording

The agent's composition root for vision: it picks the frame source (webcam,
paced replay, or a clip on clip time), the detector (local YOLO; the remote
GPU service plugs in as the failover primary once it exists, ADR 0007), the
marker catalog for held products, and where snapshots go (the backend, a
JSONL recording, or both).
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import statistics
import time
from datetime import datetime, timedelta
from pathlib import Path

from agent.backend_link import BackendLink
from agent.vision.adapters import ArucoMarkerDetector, UltralyticsDetector
from agent.vision.detector import CompositeDetector, Frame
from agent.vision.held import HeldProductTracker, MarkerCatalog
from agent.vision.pipeline import JsonlRecorder, Link, VisionPipeline, blur_encoder
from agent.vision.snapshot import SnapshotBuilder, SnapshotConfig
from agent.vision.types import FrameDetections

logger = logging.getLogger("agent.vision")


class _TimedDetector:
    """Wraps a detector to collect inference times for the recording's meta file."""

    def __init__(self, inner: CompositeDetector) -> None:
        self._inner = inner
        self.name = inner.name
        self.ms: list[float] = []

    def detect(self, frame: Frame, measured_at: datetime) -> FrameDetections:
        started = time.perf_counter()
        result = self._inner.detect(frame, measured_at)
        self.ms.append((time.perf_counter() - started) * 1000)
        return result


async def run(args: argparse.Namespace) -> int:
    from agent.vision.capture import ClipSource, LatestFrameCamera, VideoFileSource

    source: LatestFrameCamera | VideoFileSource | ClipSource
    if args.record:
        if not args.video:
            raise SystemExit("--record needs --video (recordings are made from clips)")
        source = ClipSource(
            args.video, sample_fps=args.sample_fps, start_s=args.start, duration_s=args.duration
        )
    elif args.video:
        source = VideoFileSource(args.video)
    else:
        source = LatestFrameCamera(args.camera)

    local = UltralyticsDetector(
        pose_model=args.pose_model,
        object_model=args.object_model or None,
        object_every=1 if args.record else args.object_every,
        conf=args.conf,
        device=args.device,
    )
    catalog = MarkerCatalog.from_file(Path(args.markers)) if args.markers else None
    detector = _TimedDetector(CompositeDetector(local, ArucoMarkerDetector() if catalog else None))

    backend = None if args.offline else BackendLink(args.backend)
    recorder = (
        JsonlRecorder(Path(args.record), source.clip_start, forward=backend)
        if args.record and isinstance(source, ClipSource)
        else None
    )
    link: Link | None = recorder or backend
    if link is None:
        raise SystemExit("--offline needs --record (otherwise snapshots go nowhere)")

    pipeline = VisionPipeline(
        screen_id=args.screen,
        source=source,
        detector=detector,
        link=link,
        held=HeldProductTracker(catalog) if catalog else None,
        snapshots=SnapshotBuilder(
            args.screen, SnapshotConfig(debounce=timedelta(milliseconds=args.debounce_ms))
        ),
        keyframes=None if (args.no_keyframes or args.record) else blur_encoder(),
    )
    link_task = asyncio.create_task(backend.run()) if backend else None
    try:
        await pipeline.run()
    finally:
        if link_task:
            link_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await link_task
        source.close()
        if recorder:
            recorder.close()
            meta = {
                "video": Path(args.video).name,
                "pose_model": args.pose_model,
                "object_model": args.object_model,
                "sample_fps": args.sample_fps,
                "debounce_ms": args.debounce_ms,
                "snapshots": recorder.lines,
                "frames": pipeline.stats.frames,
                "infer_p50_ms": statistics.median(detector.ms) if detector.ms else None,
                "infer_max_ms": max(detector.ms, default=None),
            }
            Path(args.record).with_suffix(".meta.json").write_text(
                json.dumps(meta, indent=2) + "\n", encoding="utf-8"
            )
            print(json.dumps(meta))
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="agent.vision")
    parser.add_argument("--screen", required=True)
    parser.add_argument("--backend", default="ws://127.0.0.1:8000/ws/agent")
    parser.add_argument("--offline", action="store_true", help="don't connect to the backend")
    parser.add_argument("--camera", type=int, default=0)
    parser.add_argument("--video", help="a video file instead of the webcam")
    parser.add_argument("--record", help="write the eval JSONL (docs/evals.md §4); needs --video")
    parser.add_argument("--sample-fps", type=float, default=5.0, help="record mode only")
    parser.add_argument("--start", type=float, default=0.0, help="record mode: clip offset (s)")
    parser.add_argument("--duration", type=float, default=None, help="record mode: seconds")
    parser.add_argument("--pose-model", default="yolo26n-pose.pt")
    parser.add_argument("--object-model", default="yolo26s.pt", help="'' disables objects")
    parser.add_argument("--object-every", type=int, default=6)
    parser.add_argument("--conf", type=float, default=0.35)
    parser.add_argument("--debounce-ms", type=float, default=500.0)
    parser.add_argument("--device", default=None, help="e.g. 0, cpu, mps")
    parser.add_argument("--markers", help="JSON {marker_id: brand_slug} for the demo props")
    parser.add_argument("--no-keyframes", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
