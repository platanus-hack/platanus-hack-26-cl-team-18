"""Latest-wins buffer between a socket reader and its worker.

"Drop stale frames, never queue them": a new item with the same conflation key
replaces the pending one (a fresher audience snapshot supersedes the old one),
and when the buffer is full the oldest pending item is dropped. Items that must
not conflate (utterances) use a unique key such as `(type, seq)`.

Single event loop only; not thread-safe.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Hashable
from dataclasses import dataclass


@dataclass
class MailboxStats:
    accepted: int = 0
    conflated: int = 0
    overflowed: int = 0


class ConflatingMailbox[T]:
    def __init__(self, *, max_items: int = 32) -> None:
        if max_items < 1:
            raise ValueError("max_items must be >= 1")
        self._items: OrderedDict[Hashable, T] = OrderedDict()
        self._max_items = max_items
        self._ready = asyncio.Event()
        self.stats = MailboxStats()

    def __len__(self) -> int:
        return len(self._items)

    def put(self, key: Hashable, item: T) -> None:
        if key in self._items:
            del self._items[key]
            self.stats.conflated += 1
        elif len(self._items) >= self._max_items:
            self._items.popitem(last=False)
            self.stats.overflowed += 1
        self._items[key] = item
        self.stats.accepted += 1
        self._ready.set()

    async def get(self) -> T:
        while not self._items:
            self._ready.clear()
            await self._ready.wait()
        _, item = self._items.popitem(last=False)
        return item
