"""Drop the backlog a stalled link flushes when it comes back ("drop stale frames").

A Wi-Fi blip that doesn't reset the agent's connection keeps the agent's writes in
its kernel buffer; when the link heals they all arrive at once, in order, each
newer than the last. `SeqGate` and the loop's ordering accept every one, so the
screen would re-decide on readings that are seconds old ("time travel": the
house ad the silent link earned, then the old group again, then the real room).

Per connection, the gate learns the link's normal lag (`received_at -
measured_at`: clock offset plus transit) as its running minimum, and drops a
reading that arrives more than `max_delay` later than that. A lag that stays high
for `rebase_after` (a clock that really moved, a slower path) becomes the new
normal, so the gate never locks the loop out. Single event loop; not thread-safe.
"""

from __future__ import annotations

from collections.abc import Hashable
from dataclasses import dataclass
from datetime import datetime, timedelta

# Above the agent's own outbox freshness (1.5 s, backend_link.py) with room for the
# local CPU detector's slower frames after a GPU failover (ADR 0007).
MAX_DELAY = timedelta(seconds=2)
REBASE_AFTER = timedelta(seconds=3)


@dataclass(slots=True)
class _Lag:
    floor: timedelta
    late_since: datetime | None = None  # server instant of the first late reading in a row
    late_min: timedelta | None = None  # the smallest lag seen in that run


class FreshnessGate:
    def __init__(
        self, *, max_delay: timedelta = MAX_DELAY, rebase_after: timedelta = REBASE_AFTER
    ) -> None:
        self._max_delay = max_delay
        self._rebase_after = rebase_after
        self._lags: dict[Hashable, _Lag] = {}
        self.dropped = 0

    def admit(self, stream: Hashable, *, measured_at: datetime, received_at: datetime) -> bool:
        # A device clock ahead of ours gives a negative lag. Never learn it as the
        # floor: one reading from a clock 9 s ahead would make every on-time reading
        # after it look 9 s late (a minor's reading among them).
        lag = max(received_at - measured_at, timedelta(0))
        state = self._lags.get(stream)
        if state is None or lag <= state.floor:
            self._lags[stream] = _Lag(floor=lag)
            return True
        if lag - state.floor <= self._max_delay:
            state.late_since = state.late_min = None
            return True
        if state.late_since is None or state.late_min is None:
            state.late_since, state.late_min = received_at, lag
        else:
            state.late_min = min(state.late_min, lag)
            if received_at - state.late_since >= self._rebase_after:
                # Late for a while without a fresh reading in between: the link's
                # normal moved (a slower path, a clock that changed). Follow it.
                self._lags[stream] = _Lag(floor=state.late_min)
                return lag - state.late_min <= self._max_delay
        self.dropped += 1
        return False
