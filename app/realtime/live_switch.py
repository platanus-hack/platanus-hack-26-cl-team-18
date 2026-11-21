"""The station's "En vivo" switch: per screen, is the loop sensing and generating?

Two independent axes per screen (never one combined enum):

- `live`: the master switch. Off = standby: the camera agent stays warm but
  analyses nothing, every audience message for the screen is dropped at the
  socket, no warm AI job runs, and the screen falls back to the house ad.
- `mic`: the operator's choice for the microphone while live.

Everything else is derived, so it can't go stale: `sensing = live`,
`listening = live and mic and speech_opt_in` (ADR 0005: speech stays opt-in per
screen via SPEECH_MODE_SCREENS) and `ai_generation = live`.

The state lives in process memory (single worker, ADR 0006). It is station
control, not a business record: a backend restart returns to `starts_live`.
Listeners run on the caller's thread, after the lock is released.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from threading import Lock

from app.contract.messages import AgentRole, LiveSet, LiveStatus
from app.modules.common.application.clock import BaseClock

type LiveListener = Callable[[LiveStatus, LiveStatus], None]
type AgentRoles = Callable[[str], Iterable[AgentRole]]


@dataclass(frozen=True, slots=True)
class _State:
    live: bool
    mic: bool
    revision: int
    changed_at: datetime


class LiveSwitchboard:
    def __init__(
        self,
        *,
        clock: BaseClock,
        starts_live: bool,
        speech_screens: Iterable[str] = (),
        agents: AgentRoles = lambda _screen: (),
        capacity: int = 64,
    ) -> None:
        if capacity < 1:
            raise ValueError("live switch capacity must be positive")
        self._clock = clock
        self._starts_live = starts_live
        self._speech_screens = frozenset(speech_screens)
        self._agents = agents
        self._capacity = capacity
        self._screens: OrderedDict[str, _State] = OrderedDict()
        self._listeners: list[LiveListener] = []
        self._lock = Lock()
        self._started_at = clock.now_utc()
        # Agent messages dropped at the socket while in standby (diagnostics only).
        self.dropped = 0

    def on_change(self, listener: LiveListener) -> None:
        self._listeners.append(listener)

    def bind_agents(self, agents: AgentRoles) -> None:
        self._agents = agents

    # ------------------------------------------------------------ reads (hot path)

    def sensing(self, screen_id: str) -> bool:
        return self._state(screen_id).live

    def ai_generation(self, screen_id: str) -> bool:
        return self._state(screen_id).live

    def listening(self, screen_id: str) -> bool:
        state = self._state(screen_id)
        return state.live and state.mic and screen_id in self._speech_screens

    def status(self, screen_id: str) -> LiveStatus:
        return self._status(screen_id, self._state(screen_id))

    def statuses(self) -> list[LiveStatus]:
        with self._lock:
            known = list(self._screens.items())
        return [self._status(screen_id, state) for screen_id, state in known]

    # ------------------------------------------------------------ writes

    def set(self, message: LiveSet) -> LiveStatus:
        """Apply the operator's switch; returns the resulting status.

        A no-op (same values) keeps the revision and notifies nobody, so a
        retried or double-clicked request has no side effect.
        """
        with self._lock:
            before = self._screens.get(message.screen_id) or self._initial()
            live = before.live if message.live is None else message.live
            mic = before.mic if message.mic is None else message.mic
            if (live, mic) == (before.live, before.mic):
                self._remember(message.screen_id, before)
                return self._status(message.screen_id, before)
            after = _State(
                live=live, mic=mic, revision=before.revision + 1, changed_at=self._clock.now_utc()
            )
            self._remember(message.screen_id, after)
        previous = self._status(message.screen_id, before)
        current = self._status(message.screen_id, after)
        for listener in self._listeners:
            listener(previous, current)
        return current

    # ------------------------------------------------------------ internals

    def _initial(self) -> _State:
        return _State(live=self._starts_live, mic=True, revision=0, changed_at=self._started_at)

    def _state(self, screen_id: str) -> _State:
        with self._lock:
            return self._screens.get(screen_id) or self._initial()

    def _remember(self, screen_id: str, state: _State) -> None:
        self._screens[screen_id] = state
        self._screens.move_to_end(screen_id)
        while len(self._screens) > self._capacity:
            self._screens.popitem(last=False)

    def _status(self, screen_id: str, state: _State) -> LiveStatus:
        opt_in = screen_id in self._speech_screens
        return LiveStatus(
            screen_id=screen_id,
            revision=state.revision,
            live=state.live,
            mic=state.mic,
            speech_opt_in=opt_in,
            sensing=state.live,
            listening=state.live and state.mic and opt_in,
            ai_generation=state.live,
            agents=sorted(set(self._agents(screen_id))),
            changed_at=state.changed_at,
        )
