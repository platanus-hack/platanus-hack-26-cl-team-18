"""RemoteGpuDetector against the real `gpu/` service (uvicorn subprocess, fake model).

Needs the gpu project synced (`cd gpu && uv sync`) and OpenCV (vision extra);
skips otherwise.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from agent.vision.adapters import _objects, _persons
from agent.vision.detector import DetectorUnavailable, FailoverDetector
from agent.vision.remote import RemoteGpuDetector
from agent.vision.types import FrameDetections

cv2 = pytest.importorskip("cv2")

GPU_DIR = Path(__file__).resolve().parents[2] / "gpu"
GPU_PYTHON = GPU_DIR / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
if not GPU_PYTHON.exists():
    pytest.skip("gpu project not synced (cd gpu && uv sync)", allow_module_level=True)

T0 = datetime(2026, 11, 21, 15, tzinfo=UTC)
TOKEN = "test-token"
# What the fake model answers, in the pixels of the 640x360 JPEG the client sends.
CANNED: dict[str, Any] = {
    "persons": [
        {
            "box": [100.0, 20.0, 220.0, 350.0],
            "confidence": 0.875,
            "keypoints": [[160.0 + i, 60.0 + i, 0.5 + i / 64] for i in range(17)],
        }
    ],
    "objects": [{"label": "cup", "box": [300.0, 200.0, 330.0, 240.0], "confidence": 0.625}],
}


@dataclass
class Server:
    url: str
    port: int
    process: subprocess.Popen[bytes]

    def stop(self) -> None:
        self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def start_server(tmp: Path, *, port: int | None = None, **env: str) -> Server:
    port = port or _free_port()
    canned = tmp / "canned.json"
    canned.write_text(json.dumps(CANNED), encoding="utf-8")
    full_env = {k: v for k, v in os.environ.items() if not k.startswith(("GPU_", "VIRTUAL_ENV"))}
    full_env |= {"GPU_MODEL": "fake", "GPU_FAKE_DETECTIONS": str(canned), **env}
    process = subprocess.Popen(
        [
            *(str(GPU_PYTHON), "-m", "uvicorn", "service.app:app", "--host", "127.0.0.1"),
            *("--port", str(port), "--no-access-log", "--ws-per-message-deflate", "false"),
        ],
        cwd=GPU_DIR,
        env=full_env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=0.5) as r:
                if json.load(r)["status"] == "ok":
                    return Server(f"ws://127.0.0.1:{port}/infer", port, process)
        except OSError:
            time.sleep(0.1)
    process.kill()
    raise RuntimeError("gpu service did not start")


@pytest.fixture(scope="module")
def fast(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Server]:
    server = start_server(tmp_path_factory.mktemp("fast"), GPU_TOKEN=TOKEN)
    yield server
    server.stop()


@pytest.fixture(scope="module")
def slow(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Server]:
    server = start_server(tmp_path_factory.mktemp("slow"), GPU_FAKE_DELAY_MS="500")
    yield server
    server.stop()


def camera_frame() -> np.ndarray:
    rng = np.random.default_rng(0)
    return rng.integers(0, 255, size=(720, 1280, 3), dtype=np.uint8)


class LocalStub:
    name = "local"

    def __init__(self) -> None:
        self.calls = 0

    def detect(self, frame: Any, measured_at: datetime) -> FrameDetections:
        self.calls += 1
        return FrameDetections(measured_at=measured_at, width=1280, height=720)


# ------------------------------------------------------------------ round trip + parity


def _tensor(values: Any) -> SimpleNamespace:
    array = np.asarray(values, dtype=float)
    return SimpleNamespace(cpu=lambda: SimpleNamespace(numpy=lambda: array))


class _Boxes:
    def __init__(self, xyxy: Any, conf: Any, cls: Any) -> None:
        self.xyxy, self.conf, self.cls = _tensor(xyxy), _tensor(conf), _tensor(cls)
        self._n = len(conf)

    def __len__(self) -> int:
        return self._n


def local_adapter_view(scale: float) -> tuple[list[Any], list[Any]]:
    """What UltralyticsDetector would return had it seen the same people at camera size."""
    [p] = CANNED["persons"]
    [o] = CANNED["objects"]
    kps = np.asarray(p["keypoints"])
    pose = SimpleNamespace(
        boxes=_Boxes([np.asarray(p["box"]) * scale], [p["confidence"]], [0]),
        keypoints=SimpleNamespace(xy=_tensor([kps[:, :2] * scale]), conf=_tensor([kps[:, 2]])),
    )
    objects = SimpleNamespace(
        boxes=_Boxes([np.asarray(o["box"]) * scale], [o["confidence"]], [41]),
        names={0: "person", 41: "cup"},
    )
    return _persons(pose), _objects(objects)


def test_round_trip_matches_the_local_adapter_output(fast: Server) -> None:
    detector = RemoteGpuDetector(fast.url, token=TOKEN)
    try:
        result = detector.detect(camera_frame(), T0)
    finally:
        detector.close()
    persons, objects = local_adapter_view(scale=2.0)  # 1280x720 camera -> 640x360 JPEG
    assert result.persons == tuple(persons)
    assert result.objects == tuple(objects)
    assert (result.width, result.height, result.measured_at) == (1280, 720, T0)
    assert result.detector == "remote"
    assert result.inference_ms >= result.extras["round_trip_ms"] > 0
    assert set(result.extras) == {"round_trip_ms", "server_ms", "model_ms", "encode_ms", "jpeg_kb"}
    assert 1 < result.extras["jpeg_kb"] < 400


def test_objects_are_reused_between_object_frames(fast: Server) -> None:
    detector = RemoteGpuDetector(fast.url, token=TOKEN, object_every=3)
    try:
        results = [detector.detect(camera_frame(), T0) for _ in range(4)]
    finally:
        detector.close()
    assert all(r.objects and r.objects[0].label == "cup" for r in results)
    assert detector.sent == 4 and detector.in_flight == 0


def test_round_trip_latency_with_the_fake_model(fast: Server) -> None:
    detector = RemoteGpuDetector(fast.url, token=TOKEN)
    frame = camera_frame()
    try:
        trips = [detector.detect(frame, T0).extras["round_trip_ms"] for _ in range(30)]
    finally:
        detector.close()
    trips.sort()
    print(f"fake-model round trip p50={trips[15]:.1f} ms max={trips[-1]:.1f} ms")
    assert trips[15] < 80  # localhost + fake model: far under the gate


# ------------------------------------------------------------------ auth


def test_missing_or_wrong_token_is_unavailable(fast: Server) -> None:
    for token in (None, "wrong"):
        with pytest.raises(DetectorUnavailable, match="refused"):
            RemoteGpuDetector(fast.url, token=token).detect(camera_frame(), T0)


# ------------------------------------------------------------------ in flight, timeout, failover


def test_at_most_two_in_flight_then_newer_frames_are_dropped(slow: Server) -> None:
    detector = RemoteGpuDetector(slow.url, timeout_s=0.1, stale_after_s=5.0)
    frame = camera_frame()
    try:
        with pytest.raises(DetectorUnavailable, match="no reply"):
            detector.detect(frame, T0)
        with pytest.raises(DetectorUnavailable, match="no reply"):
            detector.detect(frame, T0)
        assert detector.in_flight == 2
        for _ in range(3):  # not sent, not queued
            with pytest.raises(DetectorUnavailable, match="dropped"):
                detector.detect(frame, T0)
        assert (detector.sent, detector.dropped, detector.in_flight) == (2, 3, 2)
        time.sleep(1.2)  # the server answers both late frames (500 ms each, in order)
        with pytest.raises(DetectorUnavailable, match="no reply"):
            detector.detect(frame, T0)  # late replies drained, a new frame goes out
        assert detector.late == 2 and detector.sent == 3 and detector.in_flight == 1
    finally:
        detector.close()


def test_unanswered_in_flight_frames_reset_the_socket(slow: Server) -> None:
    detector = RemoteGpuDetector(slow.url, timeout_s=0.05, stale_after_s=0.1)
    frame = camera_frame()
    try:
        for _ in range(2):
            with pytest.raises(DetectorUnavailable):
                detector.detect(frame, T0)
        time.sleep(0.15)
        with pytest.raises(DetectorUnavailable, match="reset"):
            detector.detect(frame, T0)
        assert detector.in_flight == 0
    finally:
        detector.close()


def test_timeout_fails_over_to_the_local_detector(slow: Server) -> None:
    remote = RemoteGpuDetector(slow.url, timeout_s=0.1)
    local = LocalStub()
    detector = FailoverDetector(remote, local)
    try:
        result = detector.detect(camera_frame(), T0)
    finally:
        remote.close()
    assert result.detector == "local" and local.calls == 1
    assert detector.active == "local"


def test_unreachable_service_fails_over_to_local() -> None:
    local = LocalStub()
    remote = RemoteGpuDetector(f"ws://127.0.0.1:{_free_port()}/infer", connect_timeout_s=0.5)
    detector = FailoverDetector(remote, local)
    assert detector.detect(camera_frame(), T0).detector == "local"
    assert detector.active == "local"


def test_disconnect_is_unavailable_and_reconnects_lazily(tmp_path: Path) -> None:
    server = start_server(tmp_path)
    detector = RemoteGpuDetector(server.url)
    frame = camera_frame()
    try:
        assert detector.detect(frame, T0).detector == "remote"
        server.stop()
        with pytest.raises(DetectorUnavailable):
            detector.detect(frame, T0)
        with pytest.raises(DetectorUnavailable, match="can't reach"):
            detector.detect(frame, T0)
        server = start_server(tmp_path, port=server.port)
        assert detector.detect(frame, T0).detector == "remote"
    finally:
        detector.close()
        server.stop()
