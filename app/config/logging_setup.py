"""Process-wide logging: one line per record, request-context fields appended.

Configured at app startup (lifespan), never on import. Never log secrets,
camera frames, audio, utterance text or money amounts.
"""

from __future__ import annotations

import logging

from app.config.logging_context import get_context

_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


class _ContextFormatter(logging.Formatter):
    """Appends bound context without mutating the record other handlers see."""

    def format(self, record: logging.LogRecord) -> str:
        line = super().format(record)
        ctx = get_context()
        return line + "".join(f" {k}={v}" for k, v in ctx.items()) if ctx else line


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(_ContextFormatter(_FORMAT))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
