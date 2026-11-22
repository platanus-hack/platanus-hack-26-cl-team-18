"""Process-wide logging: one line per record, request-context fields appended.

Configured at app startup (lifespan), never on import. Never log secrets,
camera frames, audio, utterance text or money amounts.
"""

from __future__ import annotations

import logging
import re

from app.config.logging_context import get_context

_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"

# Secrets that ride in URLs: the demo key in `/demo/<key>/...` paths and `?key=`/`?token=`
# query values (the `/ws/dev` socket, a first `/screen?token=` load). uvicorn logs every
# request line (`uvicorn.access`) and every WebSocket handshake (`uvicorn.error`).
_DEMO_PATH = re.compile(r"(/demo/)[^/?#\s\"']+")
_SECRET_QUERY = re.compile(r"([?&](?:key|token)=)[^&#\s\"']*", re.IGNORECASE)
REDACTED = "<redacted>"
_URL_LOGGERS = ("uvicorn.access", "uvicorn.error")


def redact_url_secrets(text: str) -> str:
    return _SECRET_QUERY.sub(rf"\1{REDACTED}", _DEMO_PATH.sub(rf"\1{REDACTED}", text))


class RedactUrlSecrets(logging.Filter):
    """Rewrites a record's message and string args; never drops a record."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_url_secrets(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(
                redact_url_secrets(arg) if isinstance(arg, str) else arg for arg in record.args
            )
        elif isinstance(record.args, dict):
            record.args = {
                key: redact_url_secrets(value) if isinstance(value, str) else value
                for key, value in record.args.items()
            }
        return True


def install_url_redaction() -> None:
    """Idempotent: attach the filter to uvicorn's request/handshake loggers."""
    for name in _URL_LOGGERS:
        target = logging.getLogger(name)
        if not any(isinstance(f, RedactUrlSecrets) for f in target.filters):
            target.addFilter(RedactUrlSecrets())


class SkipHealthChecks(logging.Filter):
    """Drops the access-log line of a successful GET /health. Render's health check and
    the keep-alive call it every few seconds: it was ~80 % of prod's log, burying the
    requests that matter. A failing health check (any other status) still logs."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 5:
            return not (args[2] == "/health" and args[4] == 200)
        return True


def quiet_health_checks() -> None:
    """Idempotent: attach SkipHealthChecks to uvicorn's access logger."""
    target = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, SkipHealthChecks) for f in target.filters):
        target.addFilter(SkipHealthChecks())


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
    install_url_redaction()
