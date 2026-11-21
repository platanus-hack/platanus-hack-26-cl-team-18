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
import math
import os
import statistics
import time
from datetime import datetime, timedelta
from pathlib import Path

from agent.backend_link import BackendLink
from agent.contract import AgentHello, AgentRole
from agent.live import LiveGate
from agent.vision.adapters import DEFAULT_OBJECT_MODEL, ArucoMarkerDetector, UltralyticsDetector
from agent.vision.detector import (
    CompositeDetector,
    Detector,
    DetectorUnavailable,
    FailoverDetector,
    Frame,
)
from agent.vision.held import HeldProductTracker, MarkerCatalog
from agent.vision.pipeline import JsonlRecorder, Link, VisionPipeline, blur_encoder
from agent.vision.remote import RemoteGpuDetector
from agent.vision.snapshot import SnapshotBuilder, SnapshotConfig
from agent.vision.types import FrameDetections

logger = logging.getLogger("agent.vision")


class _TimedDetector:
    """Wraps a detector to collect inference times for the recording's meta file."""

    def __init__(self, inner: CompositeDetector, *, collect_samples: bool = False) -> None:
        self._inner = inner
        self.name = inner.name
        self._collect_samples = collect_samples
        self.ms: list[float] = []

    def detect(self, frame: Frame, measured_at: datetime) -> FrameDetections:
        started = time.perf_counter()
        result = self._inner.detect(frame, measured_at)
        if self._collect_samples:
            self.ms.append((time.perf_counter() - started) * 1000)
        return result


async def run(args: argparse.Namespace) -> int:
    from agent.vision.association import ByteTrackAssociator
    from agent.vision.capture import ClipSource, LatestFrameCamera, VideoFileSource
    from agent.vision.tracking import PersonTracker

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

    object_interval = (
        timedelta(milliseconds=200) if not args.record and args.object_every is None else None
    )
    local = UltralyticsDetector(
        pose_model=args.pose_model,
        object_model=args.object_model or None,
        object_every=1 if args.record else (args.object_every or 6),
        object_interval=object_interval,
        conf=args.conf,
        device=args.device,
        cpu_threads=args.cpu_threads,
    )
    await asyncio.to_thread(local.warmup)
    remote: RemoteGpuDetector | None = None
    catalog = MarkerCatalog.from_file(Path(args.markers)) if args.markers else None
    main_detector: Detector = local
    if args.gpu_url:  # remote GPU primary, local hot standby (ADR 0007)
        remote = RemoteGpuDetector(
            args.gpu_url,
            token=args.gpu_token or os.environ.get("GPU_TOKEN") or None,
            timeout_s=args.gpu_timeout_ms / 1000,
            connect_timeout_s=args.gpu_connect_timeout_ms / 1000,
            object_every=1 if args.record else (args.object_every or 6),
            object_interval=object_interval,
        )
        primary_ready = True
        try:
            await asyncio.to_thread(remote.warmup)
        except DetectorUnavailable:
            primary_ready = False
            logger.warning("GPU warmup unavailable; local fallback is inference-warm")
        main_detector = FailoverDetector(remote, local, primary_ready=primary_ready)
    timing = _TimedDetector(
        CompositeDetector(main_detector, ArucoMarkerDetector() if catalog else None),
        collect_samples=bool(args.record),
    )
    detector = timing

    # The console's "En vivo" switch (agent/live.py). Recordings are offline clips:
    # they follow no switch.
    live = None if args.offline else LiveGate(args.screen, initial=not args.await_live)
    backend = (
        None
        if args.offline or live is None
        else BackendLink(
            args.backend,
            hello=AgentHello(screen_id=args.screen, role=AgentRole.VISION),
            on_status=live.apply,
        )
    )
    recorder = (
        JsonlRecorder(Path(args.record), source.clip_start, forward=backend)
        if args.record and isinstance(source, ClipSource)
        else None
    )
    link: Link | None = recorder or backend
    if link is None:
        raise SystemExit("--offline needs --record (otherwise snapshots go nowhere)")

    from agent.sound.sources import MicSoundScene, SoundSceneSource, WavSoundScene
    from agent.vision.minor import MinorConfig, MinorVote

    sound: SoundSceneSource | None = None  # sound scene (ADR 0008 D13): labels, never audio
    if args.sound_wav:
        sound = WavSoundScene(Path(args.sound_wav))
    elif args.sound:
        device = args.sound_device
        mic = MicSoundScene(device=int(device) if device and device.isdigit() else device)
        mic.start()
        sound = mic
    minor = MinorVote(
        MinorConfig(
            person_threshold=args.minor_threshold,
            window=args.minor_window,
            min_hits=args.minor_hits,
        )
    )

    pipeline = VisionPipeline(
        minor=minor,
        sound=sound,
        screen_id=args.screen,
        source=source,
        detector=detector,
        link=link,
        tracker=PersonTracker(associator=ByteTrackAssociator()),
        held=HeldProductTracker(catalog) if catalog else None,
        snapshots=SnapshotBuilder(
            args.screen, SnapshotConfig(debounce=timedelta(milliseconds=args.debounce_ms))
        ),
        keyframes=None if (args.no_keyframes or args.record) else blur_encoder(),
        live=live,
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
        if remote is not None:
            await asyncio.to_thread(remote.close)
        if sound:
            sound.close()
        if recorder:
            recorder.close()
            assert timing is not None
            samples = timing.ms
            meta = {
                "video": Path(args.video).name,
                "pose_model": args.pose_model,
                "object_model": args.object_model,
                "sample_fps": args.sample_fps,
                "debounce_ms": args.debounce_ms,
                "snapshots": recorder.lines,
                "frames": pipeline.stats.frames,
                "infer_samples": len(samples),
                "infer_p50_ms": statistics.median(samples) if samples else None,
                "infer_p95_ms": statistics.quantiles(samples, n=20, method="inclusive")[-1]
                if len(samples) > 1
                else next(iter(samples), None),
                "infer_max_ms": max(samples, default=None),
            }
            Path(args.record).with_suffix(".meta.json").write_text(
                json.dumps(meta, indent=2) + "\n", encoding="utf-8"
            )
            print(json.dumps(meta))
    return 0


def _positive_milliseconds(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("must be positive, finite milliseconds")
    return number


def argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent.vision")
    parser.add_argument("--screen", required=True)
    parser.add_argument("--backend", default="ws://127.0.0.1:8000/ws/agent")
    parser.add_argument("--offline", action="store_true", help="don't connect to the backend")
    parser.add_argument(
        "--await-live",
        action="store_true",
        help="start in standby: sense nothing until the console's live switch is on",
    )
    parser.add_argument("--camera", type=int, default=0)
    parser.add_argument("--video", help="a video file instead of the webcam")
    parser.add_argument("--record", help="write the eval JSONL (docs/evals.md §4); needs --video")
    parser.add_argument("--sample-fps", type=float, default=5.0, help="record mode only")
    parser.add_argument("--start", type=float, default=0.0, help="record mode: clip offset (s)")
    parser.add_argument("--duration", type=float, default=None, help="record mode: seconds")
    parser.add_argument("--pose-model", default="yolo26n-pose.pt")
    parser.add_argument(
        "--object-model", default=DEFAULT_OBJECT_MODEL, help="YOLOE-26 weights; '' disables objects"
    )
    parser.add_argument(
        "--object-every",
        type=int,
        default=None,
        help="override 5 Hz object deadline with every N frames",
    )
    parser.add_argument(
        "--cpu-threads",
        type=int,
        default=min(4, os.cpu_count() or 1),
        help="local CPU inference threads; benchmark on the venue device",
    )
    parser.add_argument("--conf", type=float, default=0.35)
    parser.add_argument("--debounce-ms", type=float, default=500.0)
    parser.add_argument("--sound", action="store_true", help="sound scene from the mic")
    parser.add_argument("--sound-wav", help="sound scene from a 16 kHz mono WAV (replay)")
    parser.add_argument("--sound-device", default=None, help="sounddevice input name or index")
    parser.add_argument("--minor-threshold", type=float, default=0.5, help="child-size evidence")
    parser.add_argument("--minor-window", type=int, default=12, help="minor vote window (frames)")
    parser.add_argument("--minor-hits", type=int, default=7, help="hits needed in the window")
    parser.add_argument("--device", default=None, help="e.g. 0, cpu, mps")
    parser.add_argument("--markers", help="JSON {marker_id: brand_slug} for the demo props")
    parser.add_argument("--no-keyframes", action="store_true")
    parser.add_argument("--gpu-url", help="remote GPU service, e.g. wss://host/infer (ADR 0007)")
    parser.add_argument("--gpu-token", default=None, help="bearer token (default: env GPU_TOKEN)")
    parser.add_argument(
        "--gpu-timeout-ms",
        type=_positive_milliseconds,
        default=150.0,
        help="GPU reply deadline before local fallback (default: 150 ms)",
    )
    parser.add_argument(
        "--gpu-connect-timeout-ms",
        type=_positive_milliseconds,
        default=500.0,
        help="GPU connection deadline before local fallback (default: 500 ms)",
    )
    return parser


def main() -> None:
    args = argument_parser().parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
