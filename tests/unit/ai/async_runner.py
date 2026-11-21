from __future__ import annotations

import asyncio
import threading
from collections.abc import Coroutine
from typing import TypeVar

T = TypeVar("T")


def run_async(coro: Coroutine[object, object, T]) -> T:
    """Run a coroutine outside pytest's worker thread event-loop state."""
    result: list[T] = []
    errors: list[BaseException] = []

    def _target() -> None:
        try:
            result.append(asyncio.run(coro))
        except BaseException as exc:  # pragma: no cover - re-raised in caller thread
            errors.append(exc)

    thread = threading.Thread(target=_target, daemon=False)
    thread.start()
    thread.join()
    if errors:
        raise errors[0]
    return result[0]
