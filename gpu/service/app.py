"""FastAPI app: `GET /health` and `WS /infer` (ADR 0007).

Stateless and queue-free: each connection handles one frame at a time (read →
decode → infer → reply → next read). A shared admission gate rejects other
clients while busy, before decoding. Keeping at most 2 frames in flight is
the client's job; the server never buffers frames beyond the socket.

Frames are decoded in memory and dropped when the reply is sent. Nothing is
written to disk and no frame bytes, pixels or detections are logged.

Config (env):

    GPU_TOKEN            bearer token for /infer (constant-time compare); required on a
                         non-loopback bind (see `check_exposure`)
    GPU_ALLOW_ANONYMOUS  "1" serves a non-loopback bind without a token (isolated LAN only)
    GPU_MODEL            "yolo" (default) or "fake"
    GPU_POSE_MODEL       default yolo26s-pose.pt
    GPU_OBJECT_MODEL     default yoloe-26s-seg.pt: YOLOE-26 + the shared vocabulary ("" disables objects)
    GPU_OBJECT_CONF      default 0.25 (open-vocabulary scores run lower)
    GPU_IMGSZ / GPU_CONF / GPU_DEVICE
    GPU_TENSORRT         "1" exports to TensorRT FP16 once (falls back to .pt)
    GPU_FAKE_DELAY_MS    fake model only: per-frame delay
    GPU_FAKE_DETECTIONS  fake model only: JSON file with canned persons/objects
"""

from __future__ import annotations

import asyncio
import hmac
import io
import ipaddress
import json
import logging
import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import numpy as np
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, status
from PIL import Image, UnidentifiedImageError
from starlette.concurrency import run_in_threadpool

from service.model import FakeModel, InferenceModel
from service.protocol import MAX_SIDE, FrameHeader, MalformedFrame, unpack_frame

logger = logging.getLogger("gpu.service")


def model_from_env() -> InferenceModel:
    kind = os.environ.get("GPU_MODEL", "yolo").strip().lower()
    if kind == "fake":
        return FakeModel.from_file(
            os.environ.get("GPU_FAKE_DETECTIONS"),
            delay_ms=float(os.environ.get("GPU_FAKE_DELAY_MS", "0") or 0),
        )
    from service.model import YoloModel

    return YoloModel(
        pose_model=os.environ.get("GPU_POSE_MODEL", "yolo26s-pose.pt"),
        object_model=os.environ.get("GPU_OBJECT_MODEL", "yoloe-26s-seg.pt") or None,
        imgsz=int(os.environ.get("GPU_IMGSZ", "640")),
        conf=float(os.environ.get("GPU_CONF", "0.35")),
        object_conf=float(os.environ.get("GPU_OBJECT_CONF", "0.25")),
        device=os.environ.get("GPU_DEVICE") or None,
        tensorrt=os.environ.get("GPU_TENSORRT", "0") == "1",
    )


def decode_jpeg(jpeg: bytes, header: FrameHeader) -> np.ndarray:
    """JPEG → HxWx3 BGR uint8 (Ultralytics' convention for numpy input)."""
    try:
        with Image.open(io.BytesIO(jpeg)) as img:
            if img.format != "JPEG":
                raise MalformedFrame("payload is not a JPEG", header.seq)
            if img.width > MAX_SIDE or img.height > MAX_SIDE:
                raise MalformedFrame("image too large", header.seq)
            if (img.width, img.height) != (header.width, header.height):
                raise MalformedFrame("width/height don't match the JPEG", header.seq)
            rgb = np.asarray(img.convert("RGB"))
    except (UnidentifiedImageError, OSError) as exc:
        raise MalformedFrame("JPEG can't be decoded", header.seq) from exc
    return np.ascontiguousarray(rgb[:, :, ::-1])


# A token anyone could guess protects nothing; `openssl rand -hex 16` gives 32.
MIN_TOKEN_LENGTH = 16


class AnonymousExposure(RuntimeError):
    """The service would accept raw frames from anyone who can reach it."""


def _is_loopback(host: str) -> bool:
    host = host.strip().strip("[]").lower()
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False  # "0.0.0.0", "::", a hostname or an interface name: reachable


def check_exposure(host: str, token: str | None, *, allow_anonymous: bool | None = None) -> None:
    """Fail closed: a non-loopback bind needs `GPU_TOKEN`, unless `GPU_ALLOW_ANONYMOUS=1`.

    Loopback (local dev GPU, tests) stays open. The opt-out is for a trusted,
    isolated venue LAN only and logs a loud warning.
    """
    if token:
        if len(token) < MIN_TOKEN_LENGTH and not _is_loopback(host):
            # A warning, not a refusal: a shorter token may already be deployed and
            # pinned in the agent's config; refusing would take the remote GPU down.
            logger.warning(
                "GPU_TOKEN has %d chars; use %d+ on a reachable bind (openssl rand -hex 16)",
                len(token),
                MIN_TOKEN_LENGTH,
            )
        return
    if _is_loopback(host):
        return
    if allow_anonymous is None:
        allow_anonymous = os.environ.get("GPU_ALLOW_ANONYMOUS", "").strip() == "1"
    if allow_anonymous:
        logger.warning(
            "GPU_ALLOW_ANONYMOUS=1: /infer on %s accepts raw camera frames from ANY client "
            "that can reach it, with no token. Only on an isolated venue LAN.",
            host,
        )
        return
    raise AnonymousExposure(
        f"refusing to serve /infer on {host} without GPU_TOKEN: set GPU_TOKEN (and pass it "
        "to the agent), bind --host 127.0.0.1, or set GPU_ALLOW_ANONYMOUS=1 on an isolated LAN"
    )


def _authorized(websocket: WebSocket, token: str | None) -> bool:
    if not token:
        return True
    supplied = websocket.headers.get("authorization", "")
    scheme, _, value = supplied.partition(" ")
    if scheme.lower() != "bearer":
        value = ""
    return hmac.compare_digest(value.strip().encode(), token.encode())


def create_app(model: InferenceModel | None = None, *, token: str | None = None) -> FastAPI:
    """`model`/`token` default to the env (see the module docstring)."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        loaded = model if model is not None else await run_in_threadpool(model_from_env)
        warmup = getattr(loaded, "warmup", None)
        if warmup is not None:
            await run_in_threadpool(warmup)
        app.state.model = loaded
        app.state.inference_gate = asyncio.Lock()
        logger.info("gpu service ready: model=%s auth=%s", loaded.name, bool(app.state.token))
        yield

    app = FastAPI(title="team18 gpu inference", lifespan=lifespan)
    app.state.token = token if token is not None else (os.environ.get("GPU_TOKEN") or None)

    @app.get("/health")
    async def health() -> dict[str, Any]:
        loaded = getattr(app.state, "model", None)
        return {"status": "ok" if loaded else "starting", "model": loaded.name if loaded else None}

    @app.websocket("/infer")
    async def infer(websocket: WebSocket) -> None:
        if not _authorized(websocket, app.state.token):
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        await websocket.accept()
        loaded: InferenceModel = app.state.model
        try:
            while True:
                message = await websocket.receive()
                if message["type"] == "websocket.disconnect":
                    return
                payload = message.get("bytes")
                if payload is None:
                    await websocket.send_text(_error(None, "frames must be binary messages"))
                    continue
                # Admission is checked on the event loop before decoding or
                # submitting a thread. Never queue raw frames behind YOLO's lock.
                gate: asyncio.Lock = app.state.inference_gate
                if gate.locked():
                    try:
                        header, _ = unpack_frame(payload)
                        reply = _error(header.seq, "inference busy; frame dropped")
                    except MalformedFrame as exc:
                        reply = _error(exc.seq, str(exc))
                else:
                    async with gate:
                        reply = await _handle(loaded, payload)
                await websocket.send_text(reply)
        except WebSocketDisconnect:
            return

    return app


async def _handle(model: InferenceModel, payload: bytes) -> str:
    received = time.perf_counter()
    try:
        header, jpeg = unpack_frame(payload)
        image = await run_in_threadpool(decode_jpeg, jpeg, header)
    except MalformedFrame as exc:
        return _error(exc.seq, str(exc))
    started = time.perf_counter()
    try:
        result = await run_in_threadpool(model.infer, image, objects=header.objects)
    except Exception:
        logger.exception("inference failed (seq %d)", header.seq)
        return _error(header.seq, "inference failed")
    finally:
        del image
    done = time.perf_counter()
    return json.dumps(
        {
            "seq": header.seq,
            "measured_at": header.measured_at,
            "width": header.width,
            "height": header.height,
            "persons": result.persons,
            "objects": result.objects,
            "server_ms": round((done - received) * 1000, 3),
            "model_ms": round((done - started) * 1000, 3),
        },
        separators=(",", ":"),
    )


def _error(seq: int | None, reason: str) -> str:
    return json.dumps({"seq": seq, "error": reason})


app = create_app()
