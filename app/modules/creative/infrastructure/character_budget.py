"""File-backed character budget: survives restarts, so a demo loop can't drain the account.

The ledger is a small JSON file next to the voice-over cache. Writes are
atomic (tmp + rename) and serialized with a lock.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from threading import Lock


class FileCharacterBudget:
    def __init__(self, path: Path, *, limit: int) -> None:
        if limit < 0:
            raise ValueError("budget limit cannot be negative")
        self._path = path
        self._limit = limit
        self._lock = Lock()
        path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def limit(self) -> int:
        return self._limit

    def spent(self) -> int:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            return max(0, int(data.get("spent", 0)))
        except FileNotFoundError:
            return 0
        except ValueError, TypeError, AttributeError:
            # A corrupt ledger must fail closed: treat the budget as spent.
            return self._limit

    def remaining(self) -> int:
        return max(0, self._limit - self.spent())

    def spend(self, characters: int) -> None:
        if characters < 0:
            raise ValueError("cannot spend a negative number of characters")
        with self._lock:
            total = self.spent() + characters
            fd, tmp = tempfile.mkstemp(dir=self._path.parent, prefix=".budget-")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump({"spent": total}, fh)
                os.replace(tmp, self._path)
            except BaseException:
                Path(tmp).unlink(missing_ok=True)
                raise
