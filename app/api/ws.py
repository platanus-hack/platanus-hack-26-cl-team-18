"""WebSocket endpoints: validate -> stamp `received_at` -> gate -> route. No business logic.

- `/ws/agent`            perception agent <-> backend (snapshots, keyframes, utterances in;
                         `live.status` out after the agent's `agent.hello`)
- `/ws/screen/{screen_id}` player <-> backend (creatives out, playback reports in)
- `/ws/console`          backend -> operator console
- `/ws/dev`              backend -> `/dev` QA console: console messages + `dev.*` (never prod;
                         demo key as `?key=`, app/api/demo_access.py)

An invalid frame is answered with `system.error` on the same socket; the
socket stays open (a bad frame must not take the screen down).

A vision agent that said `agent.hello` and then dies, hangs or loses its socket
can't report the room emptying, so its last moment would stay on air. When its
socket closes (and no other vision agent serves the screen), or no snapshot
arrives for `AGENT_SILENCE` (it heartbeats every second), the backend routes
one empty-room snapshot itself: the normal decision path then applies the leave
grace (D5.5) before the house ad, and attention stops at the last reading.
Scripts and replays never say hello, so their sparse snapshots are unaffected.
"""

from __future__ import annotations

import logging
import re
from collections.abc import MutableMapping
from datetime import datetime, timedelta
from typing import Any

import anyio
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status
from pydantic import ValidationError

from app.api.demo_access import demo_allowed
from app.bootstrap.container import Container
from app.contract.messages import (
    AGENT_INBOUND,
    PLAYER_INBOUND,
    AgentHello,
    AgentRole,
    AudienceKeyframe,
    AudienceSnapshot,
    AudienceUtterance,
    DevPerception,
    ErrorMessage,
    MinorLikely,
    PlaybackReport,
    RecognitionSnapshot,
)
from app.realtime.mailbox import ConflatingMailbox
from app.realtime.router import Inbound, Received
from app.realtime.seq_gate import SeqGate

logger = logging.getLogger(__name__)
router = APIRouter()

_SCREEN_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
AgentMessage = (
    AudienceSnapshot
    | AudienceKeyframe
    | AudienceUtterance
    | RecognitionSnapshot
    | AgentHello
    | DevPerception
)
DEV_DISABLED = 4404  # close code: the dev console is off in this environment
AGENT_SILENCE = timedelta(seconds=5)  # five missed 1 s heartbeats: the agent is gone
SILENCE_POLL_S = 0.5
# The empty reading is dated just after the last real one (no silent attention is
# billed), but never further back than the loop's measurement-skew bound accepts.
_EMPTY_MAX_AGE = timedelta(seconds=8)


def _admitted_live(c: Container, message: AgentMessage) -> bool:
    """The "En vivo" gate: in standby nothing sensed reaches the loop (live_switch.py)."""
    if isinstance(message, AudienceUtterance):
        return c.live.listening(message.screen_id)
    return c.live.sensing(message.screen_id)


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


def _empty_room(c: Container, screen_id: str, seq: int) -> tuple[AudienceSnapshot, Received] | None:
    """The reading a silent vision agent can no longer send, or None if nothing to clear."""
    last = c.input_activity.last_snapshot(screen_id)
    if last is None or not c.live.sensing(screen_id):
        return None
    now = c.clock.now_utc()
    measured_at = max(last[1] + timedelta(milliseconds=1), now - _EMPTY_MAX_AGE)
    snapshot = AudienceSnapshot(
        screen_id=screen_id,
        seq=seq,
        measured_at=measured_at,
        people=0,
        looking=0,
        minor_likely=MinorLikely(value=False, confidence=0.0),
    )
    return snapshot, Received(received_at=now)


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
    vision_screens: set[str] = set()  # screens this socket said hello for as `vision`
    last_seq: dict[str, int] = {}
    emptied: dict[str, datetime] = {}  # screen -> the real reading already cleared

    def empty_room(screen_id: str) -> tuple[AudienceSnapshot, Received] | None:
        last = c.input_activity.last_snapshot(screen_id)
        if last is None or emptied.get(screen_id) == last[0]:
            return None
        empty = _empty_room(c, screen_id, last_seq.get(screen_id, 0))
        if empty is not None:
            emptied[screen_id] = last[0]
        return empty

    async def drain() -> None:
        while True:
            message, received = await mailbox.get()
            await c.router.dispatch(message, received)

    async def watch_silence() -> None:
        while True:
            await anyio.sleep(SILENCE_POLL_S)
            now = c.clock.now_utc()
            for screen_id in sorted(vision_screens):
                last = c.input_activity.last_snapshot(screen_id)
                if last is None or now - last[0] < AGENT_SILENCE:
                    continue
                if (empty := empty_room(screen_id)) is not None:
                    logger.warning("vision agent silent: the room counts as empty")
                    mailbox.put(("audience.snapshot", screen_id), empty)

    try:
        async with anyio.create_task_group() as tg:
            tg.start_soon(drain)
            tg.start_soon(watch_silence)
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
                    if not c.live.sensing(pending_keyframe.screen_id):
                        c.live.dropped += 1  # standby: the frame bytes go nowhere
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
                if isinstance(message, AgentHello):
                    if message.role is AgentRole.VISION:
                        vision_screens.add(message.screen_id)
                    c.hub.add_agent(message.screen_id, message.role, ws)
                    status = c.live.status(message.screen_id)
                    await c.hub.send_to_agents(status, sink=ws)
                    await c.hub.broadcast_live(status)
                    continue
                if isinstance(message, AudienceKeyframe):
                    pending_keyframe = message  # bytes follow; the gate applies to them
                    continue
                if isinstance(message, DevPerception):
                    # Debug geometry for `/dev` only: relayed as is, never routed or stored.
                    if c.settings.dev_console_enabled and c.live.sensing(message.screen_id):
                        await c.hub.broadcast_dev(message)
                    continue
                if not _admitted_live(c, message):
                    c.live.dropped += 1
                    continue
                if isinstance(message, (AudienceSnapshot, AudienceUtterance)):
                    c.input_activity.observe(message, received_at)
                if isinstance(message, AudienceSnapshot):
                    last_seq[message.screen_id] = message.seq
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
                if isinstance(message, AudienceUtterance):
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
    finally:
        with anyio.CancelScope(shield=True):
            for screen_id in c.hub.remove_agent(ws):
                await c.hub.broadcast_live(c.live.status(screen_id))
            for screen_id in sorted(vision_screens):
                if AgentRole.VISION in c.hub.agent_roles(screen_id):
                    continue  # another vision agent still reports this room
                if (empty := empty_room(screen_id)) is not None:
                    await c.router.dispatch(*empty)


# ------------------------------------------------------------------ player


@router.websocket("/ws/screen/{screen_id}")
async def screen_socket(ws: WebSocket, screen_id: str) -> None:
    if not _SCREEN_ID.match(screen_id):
        await ws.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    c = _container(ws)
    await ws.accept()
    c.hub.add_player(
        screen_id,
        ws,
        attention=ws.query_params.get("attention") == "1",
        live=ws.query_params.get("live") == "1",
    )
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
            # Billing owns report identity and conflicts. A transport
            # high-water mark would discard a retry after a handler failure.
            if isinstance(report, PlaybackReport) or gate.admit(
                (report.type, screen_id), report.seq
            ):
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
    c.hub.add_console(ws, live=ws.query_params.get("live") == "1")
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


# ------------------------------------------------------------------ dev / QA


@router.websocket("/ws/dev")
async def dev_socket(ws: WebSocket) -> None:
    c = _container(ws)
    await ws.accept()
    # Demo surface (app/api/demo_access.py): a socket can't send headers, so `?key=`.
    if not c.settings.dev_console_enabled or not demo_allowed(
        c.settings, ws.query_params.get("key")
    ):
        await ws.close(code=DEV_DISABLED, reason="dev console disabled")
        return
    c.hub.add_dev(ws)
    try:
        # Same catch-up as a console: the latest decision, book and live status.
        await c.router.console_connected(ws)
        while (frame := await _next_frame(ws)) is not None:
            if frame.get("text") is not None:
                await _send_error(ws, "dev.read_only", "the dev socket is push-only")
    except WebSocketDisconnect:
        pass
    finally:
        c.hub.remove_dev(ws)
