"""`RemoteGpuDetector`: the `Detector` port over the `gpu/` inference service (ADR 0007).

Synchronous (the vision loop already runs `detect` in a worker thread), one
persistent WebSocket, connected lazily and reconnected lazily after a drop.

Per call: resize the frame to a 640 px long side, JPEG q70, send it with a
`{seq, measured_at, width, height, objects}` header (wire format:
`gpu/README.md`), and wait up to `timeout_s` for that seq's reply.

**At most `max_in_flight` (2) frames in flight, never queued.** A frame whose
reply misses the timeout stays in flight and the call raises
`DetectorUnavailable`, so `FailoverDetector` serves it locally. Late replies
are discarded when they arrive. While 2 frames are still unanswered, newer
frames are *dropped* (not sent) with `DetectorUnavailable`; if those replies
never come, the socket is reset. A disconnect also raises
`DetectorUnavailable`.

Results are scaled back to the camera frame, parsed into the same types as
`UltralyticsDetector`, and tagged `detector="remote"`. `inference_ms` is the
whole call (encode + round trip, comparable with local inference); `extras`
holds `round_trip_ms`, `server_ms`, `model_ms`, `encode_ms` and `jpeg_kb`.
"""

from __future__ import annotations

import inspect
import json
import logging
import math
import socket
import struct
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from websockets.exceptions import ConnectionClosed, InvalidHandshake, InvalidStatus
from websockets.sync.client import ClientConnection, connect

from agent.vision.detector import DetectorUnavailable, Frame
from agent.vision.types import Box, FrameDetections, Keypoint, ObjectDetection, PersonDetection

logger = logging.getLogger(__name__)

HEADER_PREFIX = struct.Struct(">I")  # 4-byte big-endian header length (gpu/service/protocol.py)

# websockets >= 17 warns unless a long-lived sync connection opts into `legacy=True`.
_CONNECT_OPTS: dict[str, Any] = (
    {"legacy": True} if "legacy" in inspect.signature(connect).parameters else {}
)

# frame -> (jpeg bytes, sent width, sent height)
type Encoder = Callable[[Frame], tuple[bytes, int, int]]


def pack_frame(header: dict[str, Any], jpeg: bytes) -> bytes:
    raw = json.dumps(header, separators=(",", ":")).encode()
    return HEADER_PREFIX.pack(len(raw)) + raw + jpeg


def jpeg_encoder(long_side: int = 640, quality: int = 70) -> Encoder:
    """OpenCV encoder: downscale (never upscale) to `long_side`, then JPEG at `quality`."""
    import cv2  # local import: vision extra

    params = [int(cv2.IMWRITE_JPEG_QUALITY), quality]

    def encode(frame: Frame) -> tuple[bytes, int, int]:
        height, width = frame.shape[:2]
        scale = long_side / max(height, width)
        if scale < 1:
            size = (max(1, round(width * scale)), max(1, round(height * scale)))
            frame = cv2.resize(frame, size, interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", frame, params)
        if not ok:
            raise ValueError("JPEG encoding failed")
        return buf.tobytes(), int(frame.shape[1]), int(frame.shape[0])

    return encode


class RemoteGpuDetector:
    def __init__(
        self,
        url: str,
        *,
        token: str | None = None,
        timeout_s: float = 0.5,
        connect_timeout_s: float = 2.0,
        max_in_flight: int = 2,
        object_every: int = 6,
        object_interval: timedelta | None = None,
        encoder: Encoder | None = None,
        stale_after_s: float | None = None,
        name: str = "remote",
    ) -> None:
        if max_in_flight not in (1, 2):
            raise ValueError("max_in_flight must be 1 or 2")
        for label, value in (("timeout_s", timeout_s), ("connect_timeout_s", connect_timeout_s)):
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{label} must be positive and finite")
        if stale_after_s is not None and (not math.isfinite(stale_after_s) or stale_after_s <= 0):
            raise ValueError("stale_after_s must be positive and finite")
        self._url = url
        self._token = token
        self._timeout_s = timeout_s
        self._connect_timeout_s = connect_timeout_s
        self._max_in_flight = max_in_flight
        self._object_every = max(1, object_every)
        self._object_interval = object_interval
        self._objects_at: datetime | None = None
        self._encode = encoder or jpeg_encoder()
        self._stale_after_s = stale_after_s if stale_after_s is not None else 4 * timeout_s
        self._ws: ClientConnection | None = None
        self._in_flight: dict[int, float] = {}  # seq -> perf_counter at send
        self._seq = 0
        self._since_objects: int | None = None  # successful frames since the last object run
        self._last_objects: tuple[ObjectDetection, ...] = ()
        self._object_shape: tuple[int, int] | None = None
        self.sent = 0
        self.dropped = 0
        self.late = 0
        self.name = name

    @property
    def in_flight(self) -> int:
        return len(self._in_flight)

    def warmup(self) -> None:
        """Establish our GPU connection before live frames start."""
        self._connection()

    def close(self) -> None:
        self._reset()

    # ------------------------------------------------------------------ detect

    def detect(self, frame: Frame, measured_at: datetime) -> FrameDetections:
        started = time.perf_counter()
        ws = self._connection()
        self._drain(ws)
        if len(self._in_flight) >= self._max_in_flight:
            oldest = min(self._in_flight.values())
            if started - oldest > self._stale_after_s:
                self._reset()
                raise DetectorUnavailable("in-flight frames never answered; socket reset")
            self.dropped += 1
            raise DetectorUnavailable(f"{len(self._in_flight)} frames in flight; frame dropped")

        jpeg, sent_w, sent_h = self._encode(frame)
        encoded = time.perf_counter()
        height, width = frame.shape[:2]
        self._seq += 1
        seq = self._seq
        want_objects = (
            (self._objects_at is None or measured_at - self._objects_at >= self._object_interval)
            if self._object_interval is not None
            else self._since_objects is None or self._since_objects >= self._object_every - 1
        )
        want_objects = want_objects or self._object_shape != (height, width)
        header = {
            "seq": seq,
            "measured_at": measured_at.isoformat(),
            "width": sent_w,
            "height": sent_h,
            "objects": want_objects,
        }
        try:
            ws.send(pack_frame(header, jpeg))
        except (ConnectionClosed, OSError) as exc:
            self._reset()
            raise DetectorUnavailable(f"send failed: {exc}") from exc
        sent_at = time.perf_counter()
        self._in_flight[seq] = sent_at
        self.sent += 1
        reply = self._await(ws, seq, sent_at + self._timeout_s)
        returned = time.perf_counter()

        sx, sy = width / sent_w, height / sent_h
        try:
            for field in ("seq", "measured_at", "width", "height"):
                if type(reply[field]) is not type(header[field]) or reply[field] != header[field]:
                    raise ValueError("reply does not match the captured frame")
            if not isinstance(reply["persons"], list) or not isinstance(reply["objects"], list):
                raise ValueError("detection lists required")
            persons = tuple(_person(p, sx, sy) for p in reply["persons"])
            objects = tuple(_object(o, sx, sy) for o in reply["objects"])
            server_ms = _number(reply.get("server_ms", 0.0), minimum=0)
            model_ms = _number(reply.get("model_ms", 0.0), minimum=0)
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            self._reset()
            # Provider payloads never enter logs or exception messages.
            raise DetectorUnavailable("invalid GPU detection reply") from exc
        if want_objects:
            self._last_objects = objects
            self._since_objects = 0
            self._objects_at = measured_at
            self._object_shape = (height, width)
        else:
            self._since_objects = (self._since_objects or 0) + 1
        return FrameDetections(
            measured_at=measured_at,
            width=int(width),
            height=int(height),
            persons=persons,
            objects=self._last_objects,
            detector=self.name,
            inference_ms=(returned - started) * 1000,
            extras={
                "round_trip_ms": (returned - sent_at) * 1000,
                "server_ms": server_ms,
                "model_ms": model_ms,
                "encode_ms": (encoded - started) * 1000,
                "jpeg_kb": len(jpeg) / 1024,
            },
        )

    # ------------------------------------------------------------------ socket

    def _connection(self) -> ClientConnection:
        if self._ws is not None:
            return self._ws
        headers = {"Authorization": f"Bearer {self._token}"} if self._token else None
        try:
            ws = connect(
                self._url,
                additional_headers=headers,
                open_timeout=self._connect_timeout_s,
                close_timeout=0.5,
                compression=None,  # JPEGs don't compress
                max_size=2**22,
                ping_interval=5.0,
                ping_timeout=5.0,
                **_CONNECT_OPTS,
            )
        except InvalidStatus as exc:
            raise DetectorUnavailable(f"gpu service refused the connection: {exc}") from exc
        except (OSError, TimeoutError, InvalidHandshake) as exc:
            raise DetectorUnavailable(f"can't reach gpu service: {exc}") from exc
        try:  # small frames + request/reply: Nagle would add tens of ms
            ws.socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass
        logger.info("connected to gpu service %s", self._url)
        self._ws = ws
        self._in_flight.clear()
        return ws

    def _reset(self) -> None:
        ws, self._ws = self._ws, None
        self._in_flight.clear()
        self._last_objects = ()
        self._since_objects = None
        self._objects_at = None
        self._object_shape = None
        if ws is not None:
            try:
                ws.close()
            except Exception:  # closing a broken socket must never mask the real error
                pass

    def _drain(self, ws: ClientConnection) -> None:
        """Discard replies that arrived after their frame timed out."""
        while self._in_flight:
            try:
                raw = ws.recv(timeout=0)
            except TimeoutError:
                return
            except ConnectionClosed as exc:
                self._reset()
                raise DetectorUnavailable(f"gpu service disconnected: {exc}") from exc
            self._settle(raw)
            self.late += 1

    def _await(self, ws: ClientConnection, seq: int, deadline: float) -> dict[str, Any]:
        while True:
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                raise DetectorUnavailable(f"no reply for frame {seq} in {self._timeout_s:.2f}s")
            try:
                raw = ws.recv(timeout=remaining)
            except TimeoutError as exc:
                raise DetectorUnavailable(
                    f"no reply for frame {seq} in {self._timeout_s:.2f}s"
                ) from exc
            except ConnectionClosed as exc:
                self._reset()
                raise DetectorUnavailable(f"gpu service disconnected: {exc}") from exc
            reply = self._settle(raw)
            if type(reply.get("seq")) is not int or reply["seq"] != seq:
                self.late += 1
                continue
            if "error" in reply:
                raise DetectorUnavailable(f"gpu service rejected frame {seq}: {reply['error']}")
            return reply

    def _settle(self, raw: str | bytes) -> dict[str, Any]:
        try:
            reply = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {}
        if not isinstance(reply, dict):
            return {}
        seq = reply.get("seq")
        if type(seq) is int:
            self._in_flight.pop(seq, None)
        return reply


# ---------------------------------------------------------------- wire -> our types


def _box(raw: list[float], sx: float, sy: float) -> Box:
    x1, y1, x2, y2 = (_number(v) for v in raw)
    return Box(x1 * sx, y1 * sy, x2 * sx, y2 * sy)


def _person(raw: dict[str, Any], sx: float, sy: float) -> PersonDetection:
    return PersonDetection(
        box=_box(raw["box"], sx, sy),
        score=_number(raw["confidence"], minimum=0, maximum=1),
        keypoints=tuple(
            Keypoint(_number(x) * sx, _number(y) * sy, _number(c, minimum=0, maximum=1))
            for x, y, c in raw.get("keypoints", [])
        ),
    )


def _object(raw: dict[str, Any], sx: float, sy: float) -> ObjectDetection:
    return ObjectDetection(
        label=str(raw["label"]),
        score=_number(raw["confidence"], minimum=0, maximum=1),
        box=_box(raw["box"], sx, sy),
    )


def _number(value: Any, *, minimum: float = -math.inf, maximum: float = math.inf) -> float:
    number = float(value)
    if not math.isfinite(number) or not minimum <= number <= maximum:
        raise ValueError("invalid detection number")
    return number
