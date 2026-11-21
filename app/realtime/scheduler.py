"""Run a callback after a delay, off the event loop (minimum-ad-seconds releases).

A seam so tests drive held swaps deterministically with a fake clock instead
of sleeping on wall-clock timers.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from threading import Timer

logger = logging.getLogger(__name__)

type Scheduler = Callable[[float, Callable[[], None]], None]


def thread_timer(delay_s: float, callback: Callable[[], None]) -> None:
    def run() -> None:
        # Not a router handler: nothing else would log a failed release or renewal.
        try:
            callback()
        except Exception:
            logger.exception("scheduled callback failed")

    timer = Timer(max(0.0, delay_s), run)
    timer.daemon = True  # never keeps the process alive at shutdown
    timer.start()
