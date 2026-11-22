from __future__ import annotations

import io
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from starlette.websockets import WebSocketDisconnect

from service.app import AnonymousExposure, check_exposure, create_app
from service.model import FakeModel, Inference, parse_objects, parse_persons
from service.protocol import HEADER_PREFIX, MalformedFrame, pack_frame, unpack_frame

MEASURED_AT = "2026-11-21T15:00:00.000+00:00"


def jpeg(width: int = 640, height: int = 360, color: tuple[int, int, int] = (40, 90, 200)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buf, format="JPEG", quality=70)
    return buf.getvalue()


def frame(seq: int = 1, *, width: int = 640, height: int = 360, **extra: Any) -> bytes:
    header = {"seq": seq, "measured_at": MEASURED_AT, "width": width, "height": height, **extra}
    return pack_frame(header, jpeg(width, height))


def client(model: FakeModel | None = None, token: str | None = None) -> TestClient:
    return TestClient(create_app(model or FakeModel(), token=token or ""))


# ------------------------------------------------------------------ round trip


def test_health_reports_the_loaded_model() -> None:
    with client() as c:
        assert c.get("/health").json() == {"status": "ok", "model": "fake"}


def test_round_trip_returns_detections_for_the_same_seq() -> None:
    model = FakeModel()
    with client(model) as c, c.websocket_connect("/infer") as ws:
        ws.send_bytes(frame(7))
        reply = json.loads(ws.receive_text())
    assert reply["seq"] == 7
    assert reply["measured_at"] == MEASURED_AT
    assert (reply["width"], reply["height"]) == (640, 360)
    [person] = reply["persons"]
    assert len(person["box"]) == 4 and len(person["keypoints"]) == 17
    assert [o["label"] for o in reply["objects"]] == ["cup"]
    assert reply["server_ms"] >= reply["model_ms"] >= 0
    assert model.seen_shapes == [(360, 640, 3)]  # decoded at the JPEG's own size


def test_objects_flag_skips_the_object_model() -> None:
    with client() as c, c.websocket_connect("/infer") as ws:
        ws.send_bytes(frame(1, objects=False))
        assert json.loads(ws.receive_text())["objects"] == []


def test_frames_on_one_connection_are_answered_in_order() -> None:
    with client() as c, c.websocket_connect("/infer") as ws:
        ws.send_bytes(frame(1))
        ws.send_bytes(frame(2))
        assert [json.loads(ws.receive_text())["seq"] for _ in range(2)] == [1, 2]


def test_decoded_image_is_bgr() -> None:
    model = SimpleNamespace(name="probe", seen=None)

    def infer(image: np.ndarray, *, objects: bool = True) -> Inference:
        model.seen = image[0, 0].tolist()
        return Inference()

    model.infer = infer
    with TestClient(create_app(model, token="")) as c, c.websocket_connect("/infer") as ws:  # type: ignore[arg-type]
        header = {"seq": 1, "measured_at": MEASURED_AT, "width": 64, "height": 32}
        ws.send_bytes(pack_frame(header, jpeg(64, 32, color=(255, 0, 0))))  # pure red
        ws.receive_text()
    b, g, r = model.seen
    assert r > 200 and b < 50 and g < 50


# ------------------------------------------------------------------ auth


def test_token_required_when_configured() -> None:
    with client(token="s3cret") as c:
        with pytest.raises(WebSocketDisconnect) as missing, c.websocket_connect("/infer"):
            pass
        assert missing.value.code == 1008
        bad = {"Authorization": "Bearer nope"}
        with pytest.raises(WebSocketDisconnect), c.websocket_connect("/infer", headers=bad):
            pass
        wrong_scheme = {"Authorization": "Basic s3cret"}
        with (
            pytest.raises(WebSocketDisconnect),
            c.websocket_connect("/infer", headers=wrong_scheme),
        ):
            pass
        good = {"Authorization": "Bearer s3cret"}
        with c.websocket_connect("/infer", headers=good) as ws:
            ws.send_bytes(frame(1))
            assert json.loads(ws.receive_text())["seq"] == 1


def test_no_token_configured_allows_anonymous() -> None:
    with client() as c, c.websocket_connect("/infer") as ws:
        ws.send_bytes(frame(1))
        assert "error" not in json.loads(ws.receive_text())


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1", "[::1]", "127.0.0.2"])
def test_loopback_bind_may_run_without_a_token(host: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GPU_ALLOW_ANONYMOUS", raising=False)
    check_exposure(host, None)


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.1.50", "gpu-box", ""])
def test_reachable_bind_without_a_token_fails_closed(
    host: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GPU_ALLOW_ANONYMOUS", raising=False)
    with pytest.raises(AnonymousExposure, match="GPU_TOKEN"):
        check_exposure(host, None)
    check_exposure(host, "s3cret")  # a token makes it fine


def test_anonymous_opt_out_is_explicit_and_loud(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("GPU_ALLOW_ANONYMOUS", "true")  # only "1" opts out
    with pytest.raises(AnonymousExposure):
        check_exposure("0.0.0.0", None)
    monkeypatch.setenv("GPU_ALLOW_ANONYMOUS", "1")
    with caplog.at_level(logging.WARNING, logger="gpu.service"):
        check_exposure("0.0.0.0", None)
    assert "GPU_ALLOW_ANONYMOUS" in caplog.text


def test_cli_refuses_to_start_on_all_interfaces_without_a_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from service import __main__ as cli

    started: list[Any] = []
    monkeypatch.delenv("GPU_TOKEN", raising=False)
    monkeypatch.delenv("GPU_ALLOW_ANONYMOUS", raising=False)
    monkeypatch.setattr(cli.uvicorn, "run", lambda *a, **k: started.append(k))
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--host", "0.0.0.0"])
    assert exit_info.value.code == 2 and not started
    cli.main([])  # loopback by default: a local dev GPU still starts
    assert started[-1]["host"] == "127.0.0.1"
    monkeypatch.setenv("GPU_TOKEN", "s3cret")
    cli.main(["--host", "0.0.0.0"])
    assert started[-1]["host"] == "0.0.0.0"


# ------------------------------------------------------------------ malformed frames


@pytest.mark.parametrize(
    ("payload", "seq"),
    [
        (b"\x00\x00", None),  # shorter than the prefix
        (HEADER_PREFIX.pack(9999) + b"{}", None),  # length past the end
        (HEADER_PREFIX.pack(3) + b"xyz" + jpeg(), None),  # header not JSON
        (
            pack_frame(
                {"seq": -1, "measured_at": MEASURED_AT, "width": 640, "height": 360}, jpeg()
            ),
            None,
        ),
        (pack_frame({"seq": 3, "width": 640, "height": 360}, jpeg()), 3),  # no measured_at
        (pack_frame({"seq": 4, "measured_at": MEASURED_AT, "width": 0, "height": 360}, jpeg()), 4),
        (
            pack_frame(
                {"seq": 5, "measured_at": MEASURED_AT, "width": 640, "height": 360}, b"GIF89a"
            ),
            5,
        ),
        (
            pack_frame(
                {"seq": 6, "measured_at": MEASURED_AT, "width": 640, "height": 360},
                b"\xff\xd8garbage",
            ),
            6,
        ),
        (
            pack_frame({"seq": 7, "measured_at": MEASURED_AT, "width": 320, "height": 360}, jpeg()),
            7,
        ),  # size mismatch
    ],
)
def test_malformed_frames_get_an_error_and_the_connection_survives(
    payload: bytes, seq: int | None
) -> None:
    model = FakeModel()
    with client(model) as c, c.websocket_connect("/infer") as ws:
        ws.send_bytes(payload)
        reply = json.loads(ws.receive_text())
        assert reply["seq"] == seq and reply["error"]
        ws.send_bytes(frame(99))  # still serving
        assert json.loads(ws.receive_text())["seq"] == 99
    assert len(model.seen_shapes) == 1  # the bad frame never reached the model


def test_text_messages_are_rejected() -> None:
    with client() as c, c.websocket_connect("/infer") as ws:
        ws.send_text("hello")
        assert json.loads(ws.receive_text()) == {
            "seq": None,
            "error": "frames must be binary messages",
        }


def test_oversized_message_is_rejected() -> None:
    with pytest.raises(MalformedFrame, match="too large"):
        unpack_frame(b"\x00" * (2 * 1024 * 1024 + 1))


def test_model_failure_is_an_error_reply_not_a_crash() -> None:
    class Broken:
        name = "broken"

        def infer(self, image: np.ndarray, *, objects: bool = True) -> Inference:
            raise RuntimeError("cuda oom")

    with TestClient(create_app(Broken(), token="")) as c, c.websocket_connect("/infer") as ws:
        ws.send_bytes(frame(5))
        assert json.loads(ws.receive_text()) == {"seq": 5, "error": "inference failed"}


# ------------------------------------------------------------------ no storage


def test_frames_are_never_written_or_logged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    payloads = [frame(seq) for seq in range(3)]  # built before the guards go up
    monkeypatch.chdir(tmp_path)
    writes: list[str] = []
    real_open = open

    def guarded_open(file: Any, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        if any(flag in mode for flag in "wax+"):
            writes.append(str(file))
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr("builtins.open", guarded_open)
    monkeypatch.setattr(Image.Image, "save", lambda *a, **k: writes.append("PIL.save"))
    model = FakeModel()
    with caplog.at_level(logging.DEBUG), client(model) as c, c.websocket_connect("/infer") as ws:
        for payload in payloads:
            ws.send_bytes(payload)
            ws.receive_text()
        app_state = vars(c.app.state)["_state"]  # type: ignore[attr-defined]
    assert writes == []
    assert list(tmp_path.iterdir()) == []
    assert all(isinstance(shape, tuple) for shape in model.seen_shapes)  # shapes, not pixels
    assert not any(isinstance(v, bytes | np.ndarray) for v in app_state.values())
    assert "\\xff\\xd8" not in caplog.text and MEASURED_AT not in caplog.text


# ------------------------------------------------------------------ YOLO parsing parity


def _tensor(values: Any) -> SimpleNamespace:
    array = np.asarray(values, dtype=float)
    return SimpleNamespace(cpu=lambda: SimpleNamespace(numpy=lambda: array))


class _Boxes:
    def __init__(self, xyxy: list[list[float]], conf: list[float], cls: list[int] | None = None):
        self.xyxy, self.conf = _tensor(xyxy), _tensor(conf)
        self.cls = _tensor(cls or [0] * len(conf))
        self._n = len(conf)

    def __len__(self) -> int:
        return self._n


def test_parse_persons_matches_the_edge_adapter_shape() -> None:
    kxy = np.arange(34, dtype=float).reshape(1, 17, 2)
    result = SimpleNamespace(
        boxes=_Boxes([[10, 20, 110, 320]], [0.87]),
        keypoints=SimpleNamespace(xy=_tensor(kxy), conf=_tensor(np.full((1, 17), 0.9))),
    )
    [p] = parse_persons(result)
    assert p["box"] == [10.0, 20.0, 110.0, 320.0]
    assert p["confidence"] == pytest.approx(0.87)
    assert p["keypoints"][1] == [2.0, 3.0, pytest.approx(0.9)]
    no_conf = SimpleNamespace(
        boxes=_Boxes([[0, 0, 1, 1]], [0.5]),
        keypoints=SimpleNamespace(xy=_tensor(np.zeros((1, 17, 2))), conf=None),
    )
    assert parse_persons(no_conf)[0]["keypoints"][0] == [0.0, 0.0, 1.0]
    assert parse_persons(SimpleNamespace(boxes=None, keypoints=None)) == []


def test_parse_objects_drops_people() -> None:
    result = SimpleNamespace(
        boxes=_Boxes([[1, 2, 3, 4], [5, 6, 7, 8]], [0.6, 0.7], [0, 41]),
        names={0: "person", 41: "cup"},
    )
    assert parse_objects(result) == [
        {"label": "cup", "box": [5.0, 6.0, 7.0, 8.0], "confidence": pytest.approx(0.7)}
    ]
    assert parse_objects(SimpleNamespace(boxes=_Boxes([], []), names={})) == []


def test_parse_objects_reports_canonical_labels_for_yoloe_prompts() -> None:
    result = SimpleNamespace(
        boxes=_Boxes([[1, 2, 3, 4], [5, 6, 7, 8]], [0.4, 0.3], [0, 1]),
        names={0: "coffee cup", 1: "energy drink can"},
    )
    assert [o["label"] for o in parse_objects(result)] == ["cup", "energy drink can"]


def test_fake_model_reads_canned_detections(tmp_path: Path) -> None:
    canned = {"persons": [{"box": [1, 2, 3, 4], "confidence": 0.5, "keypoints": []}], "objects": []}
    path = tmp_path / "canned.json"
    path.write_text(json.dumps(canned))
    out = FakeModel.from_file(str(path)).infer(np.zeros((10, 10, 3), dtype=np.uint8))
    assert out.persons == canned["persons"] and out.objects == []


def test_busy_gpu_rejects_other_clients_without_queuing_frames() -> None:
    import threading

    started, release = threading.Event(), threading.Event()

    class BlockingModel:
        name = "blocking"

        def infer(self, image: np.ndarray, *, objects: bool = True) -> Inference:
            started.set()
            assert release.wait(timeout=5)
            return Inference()

    with (
        TestClient(create_app(BlockingModel(), token="")) as c,
        c.websocket_connect("/infer") as first,
        c.websocket_connect("/infer") as second,
    ):
        first.send_bytes(frame(1))
        assert started.wait(timeout=2)
        try:
            second.send_bytes(frame(2))
            busy = json.loads(second.receive_text())
        finally:
            release.set()
        assert busy == {"seq": 2, "error": "inference busy; frame dropped"}
        assert json.loads(first.receive_text())["seq"] == 1
        second.send_bytes(frame(3))
        assert "error" not in json.loads(second.receive_text())
