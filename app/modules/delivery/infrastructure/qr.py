"""Render a redemption URL as a QR image the player can show (SVG data URL, no network)."""

from __future__ import annotations

import base64
import io

import segno


def qr_data_url(url: str) -> str:
    code = segno.make(url, error="m")
    buffer = io.BytesIO()
    # Dark on white with a 4-module quiet zone: scannable from ~1.5 m on a TV.
    code.save(buffer, kind="svg", scale=8, border=4, dark="#000000", light="#ffffff")
    return "data:image/svg+xml;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
