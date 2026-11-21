"""Day-1 gate for the remote GPU detector (docs/stack.md §8, ADR 0007).

Sends frames to a running `gpu/` service one at a time and reports the
round trip (send → detections back) p50/p95, plus the server's own time, so
network and inference can be told apart. Exit code 0 = pass, 1 = fail.

    uv run --project edge python scripts/bench/gpu.py --url ws://127.0.0.1:8080/infer --frames 200
    uv run --project edge python scripts/bench/gpu.py --url wss://host/infer --token "$GPU_TOKEN" \\
        --image some_frame.jpg --frames 300

Frames: `--image` (any JPEG/PNG; resized like the agent does, 640 px long side
q70) or, by default, a synthetic 1280x720 frame. Encoding happens once, so
the numbers are network + server only. Run it from the venue uplink.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from agent.vision.remote import jpeg_encoder, pack_frame
from websockets.sync.client import connect


def percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(q * (len(ordered) - 1)))]


def load_frame(image: str | None) -> np.ndarray:
    import cv2

    if image:
        frame = cv2.imread(image, cv2.IMREAD_COLOR)
        if frame is None:
            raise SystemExit(f"can't read {image}")
        return frame
    rng = np.random.default_rng(7)
    frame = np.full((720, 1280, 3), 90, dtype=np.uint8)
    for _ in range(6):  # a few person-ish blobs + noise, so the JPEG has a realistic size
        x, y = int(rng.integers(0, 1100)), int(rng.integers(0, 300))
        cv2.rectangle(
            frame, (x, y), (x + 160, y + 400), tuple(int(c) for c in rng.integers(0, 255, 3)), -1
        )
    noise = rng.integers(0, 40, size=frame.shape, dtype=np.uint8)
    return cv2.add(frame, noise)


def main() -> int:
    parser = argparse.ArgumentParser(prog="bench_gpu")
    parser.add_argument("--url", required=True, help="ws(s)://host:port/infer")
    parser.add_argument("--token", default=os.environ.get("GPU_TOKEN"))
    parser.add_argument("--frames", type=int, default=300)
    parser.add_argument("--warmup", type=int, default=10, help="frames sent before measuring")
    parser.add_argument("--image", help="a JPEG/PNG to send instead of a synthetic frame")
    parser.add_argument(
        "--no-objects", action="store_true", help="pose only (objects run at 5 fps live)"
    )
    parser.add_argument("--p50", type=float, default=80.0, help="pass threshold, ms")
    parser.add_argument("--p95", type=float, default=150.0, help="pass threshold, ms")
    parser.add_argument("--json", action="store_true", help="print the result as JSON")
    args = parser.parse_args()

    jpeg, width, height = jpeg_encoder()(load_frame(args.image))
    headers = {"Authorization": f"Bearer {args.token}"} if args.token else None
    trips: list[float] = []
    server: list[float] = []
    errors = 0
    measured_at = datetime.now(UTC).isoformat()
    connect_started = time.perf_counter()
    with connect(args.url, additional_headers=headers, compression=None, max_size=2**22) as ws:
        connect_ms = (time.perf_counter() - connect_started) * 1000
        ws.socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        for i in range(args.warmup + args.frames):
            header = {
                "seq": i,
                "measured_at": measured_at,
                "width": width,
                "height": height,
                "objects": not args.no_objects,
            }
            started = time.perf_counter()
            ws.send(pack_frame(header, jpeg))
            reply = json.loads(ws.recv(timeout=10))
            elapsed = (time.perf_counter() - started) * 1000
            if "error" in reply or reply.get("seq") != i:
                errors += 1
                continue
            if i >= args.warmup:
                trips.append(elapsed)
                server.append(float(reply.get("server_ms", 0.0)))

    if not trips:
        print("no successful frames", file=sys.stderr)
        return 1
    p50, p95 = percentile(trips, 0.50), percentile(trips, 0.95)
    passed = p50 <= args.p50 and p95 <= args.p95 and errors == 0
    result = {
        "url": args.url.split("?")[0],
        "frames": len(trips),
        "errors": errors,
        "jpeg_kb": round(len(jpeg) / 1024, 1),
        "connect_ms": round(connect_ms, 1),
        "round_trip_p50_ms": round(p50, 1),
        "round_trip_p95_ms": round(p95, 1),
        "round_trip_max_ms": round(max(trips), 1),
        "server_p50_ms": round(statistics.median(server), 1),
        "network_p50_ms": round(p50 - statistics.median(server), 1),
        "gate": {"p50_ms": args.p50, "p95_ms": args.p95},
        "pass": passed,
    }
    if args.json:
        print(json.dumps(result))
    else:
        print(
            f"{Path(__file__).name}: {result['frames']} frames, {result['jpeg_kb']} KB JPEG, errors={errors}"
        )
        print(
            f"  round trip p50 {result['round_trip_p50_ms']} ms (gate {args.p50:.0f}) | "
            f"p95 {result['round_trip_p95_ms']} ms (gate {args.p95:.0f}) | max {result['round_trip_max_ms']} ms"
        )
        print(
            f"  server p50 {result['server_p50_ms']} ms, network p50 {result['network_p50_ms']} ms"
        )
        print("  PASS" if passed else "  FAIL")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
