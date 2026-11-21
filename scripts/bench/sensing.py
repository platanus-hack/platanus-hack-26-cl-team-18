"""Controlled edge → backend → Chromium latency probe (no camera or live providers).

Build web first, then run:
  uv run --with playwright python scripts/bench/sensing.py --detector-ms 80
An alternate --edge-root permits before/after comparisons against a checkout.
The worker uses Python 3.12 and the real pipeline; only detection is simulated.
No frames, audio, transcripts or personal observations are saved.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def worker(args: argparse.Namespace) -> None:
    import asyncio
    import time
    from datetime import UTC, datetime, timedelta

    sys.path.insert(0, str(args.edge_root))
    from agent.backend_link import BackendLink
    from agent.vision.pipeline import VisionPipeline
    from agent.vision.types import Box, FrameDetections, Keypoint, ObjectDetection, PersonDetection

    start = time.monotonic()
    epoch = datetime.now(UTC)
    snapshots = []

    class Source:
        finished = False

        def latest(self, serial):
            elapsed = time.monotonic() - start
            if elapsed > 5.5:
                self.finished = True
                return None
            current = int(elapsed * 30) + 1
            if current <= serial:
                return None
            return (current - 1) / 30, epoch + timedelta(seconds=(current - 1) / 30), current

    class Detector:
        name = "simulated"

        def detect(self, elapsed, measured_at):
            time.sleep(args.detector_ms / 1000)
            people = []
            if 0.5 <= elapsed < 3.4:
                for x in [400, 800]:
                    cx = x + 100
                    points = [Keypoint(0, 0, 0)] * 17
                    points[0] = Keypoint(cx if elapsed < 1.8 else cx + 30, 120, 0.9)
                    points[1:7] = [
                        Keypoint(cx + 20, 100, 0.9),
                        Keypoint(cx - 20, 100, 0.9),
                        Keypoint(cx + 35, 105, 0.6),
                        Keypoint(cx - 35, 105, 0.6),
                        Keypoint(cx + 90, 200, 0.9),
                        Keypoint(cx - 90, 200, 0.9),
                    ]
                    people.append(PersonDetection(Box(x, 60, x + 200, 700), 0.9, tuple(points)))
            return FrameDetections(
                measured_at,
                1280,
                720,
                persons=tuple(people),
                objects=(
                    ObjectDetection("cup", 0.9, Box(450, 400, 500, 500)),
                    ObjectDetection("bottle", 0.9, Box(850, 400, 900, 500)),
                ),
                inference_ms=args.detector_ms,
            )

    link = BackendLink(args.backend)

    class Link:
        def send(self, message, *, binary=None):
            snapshots.append(message.model_dump(mode="json"))
            link.send(message, binary=binary)

    async def run():
        task = asyncio.create_task(link.run())
        try:
            await link.wait_connected(3)
            await VisionPipeline(
                screen_id="latency-probe", source=Source(), detector=Detector(), link=Link()
            ).run()
            await asyncio.sleep(0.1)
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    asyncio.run(run())
    print(
        json.dumps(
            {"scene_at": (epoch + timedelta(seconds=0.5)).isoformat(), "snapshots": snapshots}
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--detector-ms", type=float, default=80)
    parser.add_argument("--edge-root", type=Path, default=ROOT / "edge")
    parser.add_argument("--edge-python", type=Path, default=ROOT / "edge/.venv/bin/python")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--backend", default="")
    parser.add_argument("--out", type=Path, default=ROOT / "evals/results/sensing-benchmark.json")
    args = parser.parse_args()
    if args.worker:
        worker(args)
        return
    sys.path.insert(0, str(ROOT))
    from datetime import datetime
    from tempfile import TemporaryDirectory

    from evals.live import serve
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles
    from playwright.sync_api import sync_playwright

    from app.bootstrap.container import build_container
    from app.config.settings import AppSettings
    from app.main import create_app

    index = ROOT / "web/dist/index.html"
    if not index.exists():
        raise SystemExit("Build the player first: cd web && npm run build")
    messages, displayed, errors = [], [], []
    with TemporaryDirectory() as tmp:
        settings = AppSettings(
            app_env="test",
            demo_simulator=True,
            creative_ai_copy=False,
            audience_ai=False,
            voiceovers_enabled=False,
            media_dir=Path(tmp) / "media",
        )
        app = create_app(container=build_container(settings))
        app.mount("/assets", StaticFiles(directory=ROOT / "web/dist/assets"))
        app.get("/screen")(lambda: FileResponse(index))
        with serve(app) as host, sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))

            def on_socket(ws):
                def received(raw):
                    try:
                        payload = json.loads(raw)
                    except ValueError:
                        return
                    if payload.get("type") == "creative.ready":
                        messages.append(payload)

                def sent(raw):
                    try:
                        payload = json.loads(raw)
                    except ValueError:
                        return
                    if payload.get("type") == "player.displayed":
                        displayed.append(payload)

                ws.on("framereceived", received)
                ws.on("framesent", sent)

            page.on("websocket", on_socket)
            page.goto(f"http://{host}/screen?id=latency-probe")
            page.wait_for_timeout(300)
            result = subprocess.run(
                [
                    str(args.edge_python),
                    str(Path(__file__).resolve()),
                    "--worker",
                    "--edge-root",
                    str(args.edge_root),
                    "--detector-ms",
                    str(args.detector_ms),
                    "--backend",
                    f"ws://{host}/ws/agent",
                ],
                capture_output=True,
                text=True,
                timeout=15,
                env={k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"},
                check=False,
            )
            if result.returncode:
                raise RuntimeError(result.stderr)
            edge = json.loads(result.stdout)
            page.wait_for_timeout(500)
            browser.close()
    paid = next(message for message in messages if message["auction_id"] is not None)
    visible = next(item for item in displayed if item["creative_id"] == paid["creative_id"])
    trace = paid["trace"]

    def delta(a, b):
        return round(
            (datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds() * 1000, 2
        )

    report = {
        "scope": "Controlled synthetic detections, real edge/backend/Chromium; no live hardware or providers",
        "detector_ms": args.detector_ms,
        "edge_root": str(args.edge_root),
        "scene_to_visible_ms": delta(edge["scene_at"], visible["displayed_at"]),
        "reported_to_visible_ms": delta(
            trace.get("candidate_at") or trace["measured_at"], visible["displayed_at"]
        ),
        "backend_ms": delta(trace["received_at"], trace["ready_at"]),
        "trace": trace,
        "displayed": visible,
        "browser_errors": errors,
        **edge,
    }
    epoch = datetime.fromisoformat(edge["scene_at"])
    # Confirmation-frame delays isolate the deterministic filters from backend
    # load and browser timing. Inference completion occurs after measured_at.
    scenes = [
        ("arrival", 0, lambda s: s["people"] > 0),
        ("look_away", 1.3, lambda s: s["people"] > 0 and s["looking"] == 0),
        ("departure", 2.9, lambda s: s["people"] == 0),
    ]
    report["confirmation_frame_ms"] = {}
    for name, offset, matches in scenes:
        for snapshot in edge["snapshots"]:
            relative = (datetime.fromisoformat(snapshot["measured_at"]) - epoch).total_seconds()
            if relative >= offset and matches(snapshot):
                report["confirmation_frame_ms"][name] = round((relative - offset) * 1000, 2)
                break
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {
                key: value
                for key, value in report.items()
                if key not in {"snapshots", "trace", "displayed"}
            },
            indent=2,
        )
    )
    if errors:
        raise SystemExit("Browser errors during sensing benchmark")


if __name__ == "__main__":
    main()
