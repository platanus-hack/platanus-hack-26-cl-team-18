"""Guarded AI copy by brief signature (hot-path read, warm/cold write).

Prewarming the demo's likely briefs before the demo turns live moments into
AI-quality cache hits at hot-path speed (architecture §3, trick 4).
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass

from app.modules.creative.domain.draft import CopyDraft


@dataclass
class CacheStats:
    hits: int = 0
    misses: int = 0
    writes: int = 0


class CopyCache:
    def __init__(self, max_entries: int = 512) -> None:
        self._entries: OrderedDict[str, CopyDraft] = OrderedDict()
        self._max = max_entries
        self.stats = CacheStats()

    def get(self, signature: str) -> CopyDraft | None:
        draft = self._entries.get(signature)
        if draft is None:
            self.stats.misses += 1
            return None
        self._entries.move_to_end(signature)
        self.stats.hits += 1
        return draft.cached()

    def peek(self, signature: str) -> CopyDraft | None:
        """Read without counting (the studio counts hits when it airs the draft)."""
        return self._entries.get(signature)

    def put(self, signature: str, draft: CopyDraft) -> None:
        self._entries[signature] = draft
        self._entries.move_to_end(signature)
        self.stats.writes += 1
        while len(self._entries) > self._max:
            self._entries.popitem(last=False)

    def __contains__(self, signature: str) -> bool:
        return signature in self._entries

    def __len__(self) -> int:
        return len(self._entries)
