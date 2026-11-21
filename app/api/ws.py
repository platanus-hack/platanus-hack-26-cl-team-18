"""WebSocket endpoints: validate -> stamp `received_at` -> gate -> route. No business logic.

- `/ws/agent`            perception agent -> backend (snapshots, keyframes, utterances)
- `/ws/screen/{screen_id}` player <-> backend (creatives out, playback reports in)
- `/ws/console`          backend -> operator console

An invalid frame is answered with `system.error` on the same socket; the
socket stays open (a bad frame must not take the screen down).
"""

from __future__ import annotations

import logging
import re
from collections.abc import MutableMapping
from typing import Any

import anyio
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status
from pydantic import ValidationError

from app.bootstrap.container import Container
from app.contract.messages import (
    AGENT_INBOUND,
    PLAYER_INBOUND,
    AudienceKeyframe,
    AudienceSnapshot,
    AudienceUtterance,
    ErrorMessage,
    RecognitionSnapshot,
)
from app.realtime.mailbox import ConflatingMailbox
from app.realtime.router import Inbound, Received
from app.realtime.seq_gate import SeqGate

logger = logging.getLogger(__name__)
router = APIRouter()

_SCREEN_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
AgentMessage = AudienceSnapshot | AudienceKeyframe | AudienceUtterance | RecognitionSnapshot


def _container(ws: WebSocket) -> Container:
    container: Container = ws.app.state.container
    return container


async def _send_error(
    ws: WebSocket, code: str, message: str, rejected_type: str | None = None
) -> None:
    error = ErrorMessage(code=code, message=message[:500], rejected_type=rejected_type)
    await ws.send_text(error.model_dump_json())


def _validation_summary(exc: ValidationError) -> str:
    first = exc.errors(include_url=False, include_input=False)[:3]
    return "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in first)


def _peek_type(raw: str | bytes) -> str | None:
    match = re.search(
        rb'"type"\s*:\s*"([a-z._]{1,40})"', raw if isinstance(raw, bytes) else raw.encode()
    )
    return match.group(1).decode() if match else None


async def _next_frame(ws: WebSocket) -> MutableMapping[str, Any] | None:
    frame = await ws.receive()
    if frame["type"] == "websocket.disconnect":
        return None
    return frame


# ------------------------------------------------------------------ agent


@router.websocket("/ws/agent")
async def agent_socket(ws: WebSocket) -> None:
    c = _container(ws)
    await ws.accept()
    gate = SeqGate()
    mailbox: ConflatingMailbox[tuple[Inbound, Received]] = ConflatingMailbox(
        max_items=c.settings.ws_agent_mailbox_size
    )
    pending_keyframe: AudienceKeyframe | None = None

    async def drain() -> None:
        while True:
            message, received = await mailbox.get()
            await c.router.dispatch(message, received)

    try:
        async with anyio.create_task_group() as tg:
            tg.start_soon(drain)
            while (frame := await _next_frame(ws)) is not None:
                text, data = frame.get("text"), frame.get("bytes")
                if data is not None:
                    if pending_keyframe is None or len(data) != pending_keyframe.byte_length:
                        await _send_error(
                            ws,
                            "keyframe.unexpected_bytes",
                            "binary frame without a matching audience.keyframe header",
                        )
                        pending_keyframe = None
                        continue
                    received = Received(received_at=c.clock.now_utc(), payload=data)
                    mailbox.put(
                        ("audience.keyframe", pending_keyframe.screen_id),
                        (pending_keyframe, received),
                    )
                    pending_keyframe = None
                    continue
                if text is None:
                    continue
                if len(text) > c.settings.ws_max_frame_bytes:
                    await _send_error(ws, "frame.too_large", "frame exceeds ws_max_frame_bytes")
                    continue
                try:
                    message: AgentMessage = AGENT_INBOUND.validate_json(text)
                except ValidationError as exc:
                    await _send_error(
                        ws, "message.invalid", _validation_summary(exc), _peek_type(text)
                    )
                    continue
                received_at = c.clock.now_utc()
                if not gate.admit((message.type, message.screen_id), message.seq):
                    continue  # duplicate or stale: drop, never queue
                if isinstance(message, RecognitionSnapshot):
                    if not c.recognition.put(message, Received(received_at=received_at)):
                        continue  # stale session or sequence, even across connections
                    stored = c.recognition.get(message.screen_id, received_at)
                    if stored is None or (stored[0].session_id, stored[0].seq) != (
                        message.session_id,
                        message.seq,
                    ):
                        continue
                    message = stored[0]  # retain warm evidence in subsequent publications
                if isinstance(message, AudienceKeyframe):
                    pending_keyframe = message  # bytes arrive in the next frame
                elif isinstance(message, AudienceUtterance):
                    key = (message.type, message.screen_id, message.seq)  # never conflate speech
                    mailbox.put(key, (message, Received(received_at=received_at)))
                else:
                    mailbox.put(
                        (message.type, message.screen_id),
                        (message, Received(received_at=received_at)),
                    )
            tg.cancel_scope.cancel()
    except WebSocketDisconnect:
        pass


# ------------------------------------------------------------------ player


@router.websocket("/ws/screen/{screen_id}")
async def screen_socket(ws: WebSocket, screen_id: str) -> None:
    if not _SCREEN_ID.match(screen_id):
        await ws.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    c = _container(ws)
    await ws.accept()
    c.hub.add_player(screen_id, ws, attention=ws.query_params.get("attention") == "1")
    gate = SeqGate()
    try:
        await c.router.player_connected(screen_id, ws)
        while (frame := await _next_frame(ws)) is not None:
            text = frame.get("text")
            if text is None:
                await _send_error(
                    ws, "frame.binary_unsupported", "players send JSON text frames only"
                )
                continue
            try:
                report = PLAYER_INBOUND.validate_json(text)
            except ValidationError as exc:
                await _send_error(ws, "message.invalid", _validation_summary(exc), _peek_type(text))
                continue
            if report.screen_id != screen_id:
                await _send_error(
                    ws,
                    "message.screen_mismatch",
                    "screen_id does not match the socket",
                    report.type,
                )
                continue
            received = Received(received_at=c.clock.now_utc())
            if gate.admit((report.type, screen_id), report.seq):
                await c.router.dispatch(report, received)
    except WebSocketDisconnect:
        pass
    finally:
        c.hub.remove_player(screen_id, ws)
        await c.router.player_disconnected(screen_id)


# ------------------------------------------------------------------ console


@router.websocket("/ws/console")
async def console_socket(ws: WebSocket) -> None:
    c = _container(ws)
    await ws.accept()
    c.hub.add_console(ws)
    try:
        await c.router.console_connected(ws)
        while (frame := await _next_frame(ws)) is not None:
            if frame.get("text") is not None:
                await _send_error(
                    ws, "console.read_only", "the console socket is push-only for now"
                )
    except WebSocketDisconnect:
        pass
    finally:
        c.hub.remove_console(ws)
