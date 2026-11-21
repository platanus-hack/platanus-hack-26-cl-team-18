"""The backend's "En vivo" switch as one agent process sees it.

The backend pushes `live.status` after this agent's `agent.hello` and on every
change (app/realtime/live_switch.py). The gate is the agent's copy:

- vision pauses while `sensing` is false: frames are discarded unread, nothing
  is detected or published, and every tracklet is forgotten;
- speech closes the microphone while `listening` is false, so no audio reaches
  the speech provider (ADR 0005, CLAUDE.md: no speech recognition when off).

Until the first status arrives the gate keeps its `initial` position: open for
replays and tests (old behaviour), closed with `--await-live` so a station
started in standby never senses before the operator presses the button. After a
backend disconnect it keeps the last position it was told; the backend drops
anything sensed in standby anyway.
"""

from __future__ import annotations

import asyncio
import logging

from agent.contract import LiveStatus

logger = logging.getLogger(__name__)


class LiveGate:
    def __init__(self, screen_id: str, *, initial: bool) -> None:
        self._screen_id = screen_id
        self._sensing = asyncio.Event()
        self._listening = asyncio.Event()
        if initial:
            self._sensing.set()
            self._listening.set()
        self.revision: int | None = None

    @property
    def sensing(self) -> bool:
        return self._sensing.is_set()

    @property
    def listening(self) -> bool:
        return self._listening.is_set()

    def apply(self, status: LiveStatus) -> None:
        """Adopt a pushed status for this screen (runs on the agent's event loop)."""
        if status.screen_id != self._screen_id:
            return
        # No revision check: pushes on one socket arrive in order, and a restarted
        # backend counts from 0 again, so its status must still be adopted.
        changed = (self.sensing, self.listening) != (status.sensing, status.listening)
        self.revision = status.revision
        _set(self._sensing, status.sensing)
        _set(self._listening, status.listening)
        if changed:
            logger.info(
                "live switch: sensing=%s listening=%s (revision %d)",
                status.sensing,
                status.listening,
                status.revision,
            )

    async def wait_sensing(self) -> None:
        await self._sensing.wait()

    async def wait_listening(self) -> None:
        await self._listening.wait()


def _set(event: asyncio.Event, value: bool) -> None:
    if value:
        event.set()
    else:
        event.clear()
