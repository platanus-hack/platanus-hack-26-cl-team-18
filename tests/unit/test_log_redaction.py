"""The demo key and device tokens never reach uvicorn's access or handshake logs."""

from __future__ import annotations

import logging

import pytest

from app.config.logging_setup import (
    REDACTED,
    RedactUrlSecrets,
    install_url_redaction,
    redact_url_secrets,
)

KEY = "f00dfeedcafe0123456789abcdef0123"


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        (f"/demo/{KEY}/advertiser", f"/demo/{REDACTED}/advertiser"),
        (f"/demo/{KEY}", f"/demo/{REDACTED}"),
        (f"/ws/dev?key={KEY}", f"/ws/dev?key={REDACTED}"),
        (f"/screen?id=demo-01&token={KEY}&x=1", f"/screen?id=demo-01&token={REDACTED}&x=1"),
        (f"/ws/dev?KEY={KEY}", f"/ws/dev?KEY={REDACTED}"),
        ("/ws/screen/demo-01?attention=1", "/ws/screen/demo-01?attention=1"),
        ("/r/ABCD2345/redeem?monkey=1", "/r/ABCD2345/redeem?monkey=1"),
    ],
)
def test_url_secrets_are_redacted_and_nothing_else_changes(line: str, expected: str) -> None:
    assert redact_url_secrets(line) == expected


def test_the_access_log_line_uvicorn_formats_carries_no_key(
    caplog: pytest.LogCaptureFixture,
) -> None:
    install_url_redaction()
    install_url_redaction()  # idempotent
    access = logging.getLogger("uvicorn.access")
    assert sum(isinstance(f, RedactUrlSecrets) for f in access.filters) == 1
    with caplog.at_level(logging.INFO, logger="uvicorn.access"):
        # uvicorn's own format: client, method, path with query, http version, status.
        access.info(
            '%s - "%s %s HTTP/%s" %d',
            "127.0.0.1:5",
            "GET",
            f"/demo/{KEY}/clerk?key={KEY}",
            "1.1",
            200,
        )
    (record,) = [r for r in caplog.records if r.name == "uvicorn.access"]
    assert KEY not in record.getMessage()
    assert f"/demo/{REDACTED}/clerk?key={REDACTED}" in record.getMessage()


def test_websocket_handshake_lines_are_redacted(caplog: pytest.LogCaptureFixture) -> None:
    install_url_redaction()
    handshake = logging.getLogger("uvicorn.error")
    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        handshake.info('%s - "WebSocket %s" [accepted]', "127.0.0.1:5", f"/ws/dev?key={KEY}")
    assert all(KEY not in r.getMessage() for r in caplog.records)
