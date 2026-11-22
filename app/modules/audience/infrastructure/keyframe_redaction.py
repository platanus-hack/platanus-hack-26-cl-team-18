"""Server-side check of a scene keyframe's allow-list redaction (docs/privacy.md G1, G2).

The perception agent sends the VLM a frame that is black everywhere except the
regions it detected (people and objects), with every head black too, and
declares those regions in the header. The backend doesn't trust the agent: it
checks the pixels against the declared boxes and drops the frame on any
mismatch. A raw frame from a buggy agent build, or a frame without the
declaration, never reaches a third party.

It can't prove that no face hides inside a kept region (that would need a face
model, which the stack rules out). It proves the agent did what it declared.
"""

from __future__ import annotations

import io

from PIL import Image, ImageChops, ImageDraw

from app.contract.messages import AudienceKeyframe, RedactionBox

EDGE_MARGIN_PX = 8  # JPEG blocks ring across a box edge; skip that band
DARK_MEAN = 4.0  # mean luminance of a region that must be black (0-255)
BRIGHT_LEVEL = 40  # a pixel above this counts as visible content
BRIGHT_SHARE = 0.005  # at most 0.5 % of a black region may be ringing above it


def allowlist_redaction_holds(header: AudienceKeyframe, image: bytes) -> bool:
    """True only for a JPEG that is black outside `kept` and inside every `heads` box."""
    if header.redaction != "allowlist":
        return False
    try:
        with Image.open(io.BytesIO(image)) as decoded:
            if decoded.format != "JPEG" or not (
                16 <= decoded.width <= 4096 and 16 <= decoded.height <= 4096
            ):
                return False
            gray = decoded.convert("L")
    except Exception:
        return False
    width, height = gray.size

    def rect(box: RedactionBox, grow: int) -> tuple[int, int, int, int]:
        return (
            round(box.x1 * width) - grow,
            round(box.y1 * height) - grow,
            round(box.x2 * width) + grow - 1,
            round(box.y2 * height) + grow - 1,
        )

    outside = Image.new("L", gray.size, 255)
    draw = ImageDraw.Draw(outside)
    for box in header.kept:
        draw.rectangle(rect(box, EDGE_MARGIN_PX), fill=0)
    if not _is_black(gray, outside):
        return False

    for box in header.heads:
        x1, y1, x2, y2 = rect(box, 0)
        inset = min(EDGE_MARGIN_PX, (x2 - x1) // 4, (y2 - y1) // 4)
        inner = Image.new("L", gray.size, 0)
        ImageDraw.Draw(inner).rectangle((x1 + inset, y1 + inset, x2 - inset, y2 - inset), fill=255)
        if not _is_black(gray, inner):
            return False
    return True


def _is_black(gray: Image.Image, mask: Image.Image) -> bool:
    """Whether the pixels under a 255 mask are dark (an empty mask holds trivially)."""
    count = mask.histogram()[255]
    if count == 0:
        return True
    hist = ImageChops.multiply(gray, mask).histogram()
    # multiply zeroes the pixels outside the mask; they only ever land in bin 0.
    mean = sum(level * n for level, n in enumerate(hist)) / count
    bright = sum(hist[BRIGHT_LEVEL + 1 :]) / count
    return mean <= DARK_MEAN and bright <= BRIGHT_SHARE
