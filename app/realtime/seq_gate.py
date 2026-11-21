"""Monotonic `seq` filter per stream: duplicates and out-of-order frames are dropped.

Scoped to one connection, so a restarted agent that starts again at seq 0 is
accepted on its new socket. Durable dedupe (a retried write) is the receiving
context's job via its natural key `(screen_id, seq)`.
"""

from __future__ import annotations

from collections.abc import Hashable


class SeqGate:
    def __init__(self) -> None:
        self._last: dict[Hashable, int] = {}
        self.dropped = 0

    def admit(self, stream: Hashable, seq: int) -> bool:
        last = self._last.get(stream)
        if last is not None and seq <= last:
            self.dropped += 1
            return False
        self._last[stream] = seq
        return True
