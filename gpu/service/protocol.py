"""Wire format of `WS /infer` (ADR 0007). Pure functions, no IO.

Request: one **binary** message per frame.

    [4 bytes: header length N, unsigned big-endian][N bytes: UTF-8 JSON header][JPEG bytes]

    header = {"seq": int >= 0, "measured_at": str (ISO-8601, device clock),
              "width": int, "height": int,      # size of the JPEG, not the camera frame
              "objects": bool = true}           # run the object model on this frame

Reply: one **text** message per request, JSON.

    {"seq", "measured_at", "width", "height",
     "persons": [{"box": [x1, y1, x2, y2], "confidence": f, "keypoints": [[x, y, c], ...]}],
     "objects": [{"label": "cup", "box": [x1, y1, x2, y2], "confidence": f}],
     "server_ms": f,   # receive -> reply (decode + inference)
     "model_ms": f}    # inference only

    error = {"seq": int | null, "error": "<reason>"}

Coordinates are pixels in the JPEG the client sent; the client scales them back.
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass
from typing import Any

HEADER_PREFIX = struct.Struct(">I")
MAX_HEADER_BYTES = 4096
MAX_MESSAGE_BYTES = 2 * 1024 * 1024  # a 640 px q70 JPEG is ~35 KB; 2 MB is generous
MAX_SIDE = 4096


class MalformedFrame(ValueError):
    """The request can't be decoded. `seq` is set when the header was readable."""

    def __init__(self, reason: str, seq: int | None = None) -> None:
        super().__init__(reason)
        self.seq = seq


@dataclass(frozen=True, slots=True)
class FrameHeader:
    seq: int
    measured_at: str
    width: int
    height: int
    objects: bool = True


def pack_frame(header: dict[str, Any], jpeg: bytes) -> bytes:
    raw = json.dumps(header, separators=(",", ":")).encode()
    return HEADER_PREFIX.pack(len(raw)) + raw + jpeg


def unpack_frame(message: bytes) -> tuple[FrameHeader, bytes]:
    if len(message) > MAX_MESSAGE_BYTES:
        raise MalformedFrame("message too large")
    if len(message) < HEADER_PREFIX.size:
        raise MalformedFrame("message shorter than the length prefix")
    (size,) = HEADER_PREFIX.unpack_from(message)
    if size == 0 or size > MAX_HEADER_BYTES or HEADER_PREFIX.size + size > len(message):
        raise MalformedFrame("bad header length")
    try:
        raw = json.loads(message[HEADER_PREFIX.size : HEADER_PREFIX.size + size])
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MalformedFrame("header is not JSON") from exc
    if not isinstance(raw, dict):
        raise MalformedFrame("header is not an object")
    seq = raw.get("seq")
    if not isinstance(seq, int) or isinstance(seq, bool) or seq < 0:
        raise MalformedFrame("seq must be a non-negative integer")
    width, height, measured_at = raw.get("width"), raw.get("height"), raw.get("measured_at")
    if not isinstance(measured_at, str) or not measured_at:
        raise MalformedFrame("measured_at must be a string", seq)
    for value in (width, height):
        if not isinstance(value, int) or isinstance(value, bool) or not 0 < value <= MAX_SIDE:
            raise MalformedFrame("width/height out of range", seq)
    objects = raw.get("objects", True)
    if not isinstance(objects, bool):
        raise MalformedFrame("objects must be a boolean", seq)
    jpeg = message[HEADER_PREFIX.size + size :]
    if not jpeg.startswith(b"\xff\xd8"):
        raise MalformedFrame("payload is not a JPEG", seq)
    header = FrameHeader(seq, measured_at, int(width), int(height), objects)  # type: ignore[arg-type]
    return header, jpeg
