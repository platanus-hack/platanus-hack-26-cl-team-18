"""The backend's check of a scene keyframe's allow-list redaction (docs/privacy.md G1, G2)."""

from __future__ import annotations

import random
from datetime import UTC, datetime
from io import BytesIO

from PIL import Image

from app.contract.messages import AudienceKeyframe, RedactionBox
from app.modules.audience.infrastructure.keyframe_redaction import allowlist_redaction_holds

T0 = datetime(2026, 11, 21, 15, tzinfo=UTC)
W, H = 512, 288
BODY = RedactionBox(x1=0.1, y1=0.1, x2=0.4, y2=0.95)
HEAD = RedactionBox(x1=0.15, y1=0.1, x2=0.35, y2=0.4)


def _noise(width: int, height: int, seed: int) -> Image.Image:
    rng = random.Random(seed)
    return Image.frombytes("RGB", (width, height), rng.randbytes(width * height * 3))


def _px(box: RedactionBox) -> tuple[int, int, int, int]:
    return (round(box.x1 * W), round(box.y1 * H), round(box.x2 * W), round(box.y2 * H))


def jpeg(*, kept: list[RedactionBox], black: list[RedactionBox], seed: int = 0) -> bytes:
    """Noise inside `kept`, black elsewhere and inside `black`: what the agent sends."""
    image = Image.new("RGB", (W, H), "black")
    for box in kept:
        x1, y1, x2, y2 = _px(box)
        image.paste(_noise(x2 - x1, y2 - y1, seed), (x1, y1))
    for box in black:
        image.paste("black", _px(box))
    out = BytesIO()
    image.save(out, format="JPEG", quality=80)
    return out.getvalue()


def header(
    *, kept: list[RedactionBox], heads: list[RedactionBox], redaction: str | None = "allowlist"
) -> AudienceKeyframe:
    return AudienceKeyframe(
        screen_id="demo-01",
        seq=1,
        measured_at=T0,
        byte_length=1,
        redaction=redaction,  # type: ignore[arg-type]
        kept=kept,
        heads=heads,
    )


def test_a_frame_matching_its_declared_regions_passes() -> None:
    image = jpeg(kept=[BODY], black=[HEAD])
    assert allowlist_redaction_holds(header(kept=[BODY], heads=[HEAD]), image)


def test_an_empty_scene_that_is_all_black_passes() -> None:
    assert allowlist_redaction_holds(header(kept=[], heads=[]), jpeg(kept=[], black=[]))


def test_a_raw_unredacted_frame_is_refused() -> None:
    raw = BytesIO()
    _noise(W, H, 1).save(raw, format="JPEG")
    assert not allowlist_redaction_holds(header(kept=[BODY], heads=[HEAD]), raw.getvalue())


def test_content_outside_the_kept_regions_is_refused() -> None:
    """A person the detector missed would show up exactly like this."""
    missed = RedactionBox(x1=0.6, y1=0.2, x2=0.8, y2=0.9)
    image = jpeg(kept=[BODY, missed], black=[HEAD])
    assert not allowlist_redaction_holds(header(kept=[BODY], heads=[HEAD]), image)


def test_a_visible_head_is_refused() -> None:
    image = jpeg(kept=[BODY], black=[])  # the head region was left in
    assert not allowlist_redaction_holds(header(kept=[BODY], heads=[HEAD]), image)


def test_a_frame_without_the_declaration_is_refused() -> None:
    image = jpeg(kept=[BODY], black=[HEAD])
    assert not allowlist_redaction_holds(header(kept=[BODY], heads=[HEAD], redaction=None), image)


def test_a_non_jpeg_or_garbage_payload_is_refused() -> None:
    png = BytesIO()
    Image.new("RGB", (W, H), "black").save(png, format="PNG")
    assert not allowlist_redaction_holds(header(kept=[], heads=[]), png.getvalue())
    assert not allowlist_redaction_holds(header(kept=[], heads=[]), b"\xff\xd8garbage")
