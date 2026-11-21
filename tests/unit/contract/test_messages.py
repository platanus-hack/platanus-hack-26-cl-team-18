from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.contract.messages import (
    AGENT_INBOUND,
    ALL_MESSAGES,
    CONSOLE_OUTBOUND,
    PLAYER_OUTBOUND,
    SOCKET_UNIONS,
    AudienceSnapshot,
    CreativeReady,
    CreativeSlot,
    CreativeSpec,
    LatencyTrace,
    MinorLikely,
    MoneyDTO,
    PerceptionTiming,
    PlaybackReport,
)

T0 = datetime(2026, 11, 21, 15, 0, tzinfo=UTC)


def snapshot(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "type": "audience.snapshot",
        "v": 1,
        "screen_id": "mall-01",
        "seq": 7,
        "measured_at": T0.isoformat(),
        "people": 2,
        "looking": 1,
        "tracklets": [{"id": "t1", "dwell_s": 2.5, "looking": True, "distance_band": "near"}],
        "objects": [{"label": "sports_bottle", "score": 0.91}],
        "sound_scene": [],
        "minor_likely": {"value": False, "confidence": 0.8},
    }
    base.update(overrides)
    return base


def test_agent_union_parses_a_snapshot_by_its_type() -> None:
    msg = AGENT_INBOUND.validate_json(json.dumps(snapshot()))
    assert isinstance(msg, AudienceSnapshot)
    assert msg.measured_at == T0
    assert msg.perception is None  # older agents remain compatible


@pytest.mark.parametrize("value", [-1, float("nan"), float("inf")])
def test_perception_timing_rejects_invalid_durations(value: float) -> None:
    for field in ("inference_ms", "round_trip_ms", "primary_attempt_ms"):
        with pytest.raises(ValidationError):
            PerceptionTiming.model_validate(
                {"detector": "remote", "inference_ms": 10, field: value}
            )


@pytest.mark.parametrize(
    ("overrides", "why"),
    [
        ({"extra": 1}, "unknown fields are rejected"),
        ({"v": 2}, "an unknown version is rejected"),
        ({"measured_at": "2026-11-21T15:00:00"}, "naive instants are rejected"),
        ({"looking": 3}, "looking cannot exceed people"),
        ({"seq": -1}, "seq is non-negative"),
        ({"screen_id": "has space"}, "screen ids are url-safe"),
        ({"type": "audience.unknown"}, "unknown types are rejected"),
    ],
)
def test_invalid_snapshots_are_rejected(overrides: dict[str, object], why: str) -> None:
    with pytest.raises(ValidationError):
        AGENT_INBOUND.validate_json(json.dumps(snapshot(**overrides)))


def test_money_is_a_decimal_string_never_a_float() -> None:
    assert MoneyDTO(amount="0.0125", currency="USD").amount == "0.0125"
    with pytest.raises(ValidationError):
        MoneyDTO.model_validate({"amount": 0.0125, "currency": "USD"})
    with pytest.raises(ValidationError):
        MoneyDTO(amount="1e3", currency="USD")


def test_playback_cannot_end_before_it_starts() -> None:
    with pytest.raises(ValidationError):
        PlaybackReport(
            screen_id="s", creative_id="c", seq=1, started_at=T0, ended_at=T0 - timedelta(seconds=1)
        )


def test_creative_round_trips_through_both_outbound_unions() -> None:
    ready = CreativeReady(
        screen_id="mall-01",
        creative_id="cr-1",
        auction_id=None,
        spec=CreativeSpec(
            layout_id="solo",
            slots=[
                CreativeSlot(
                    brand_id="b1",
                    brand_name="Voltio",
                    logo_url="/kits/voltio.svg",
                    copy_text="Dale.",
                )
            ],
            duration_ms=8000,
        ),
        trace=LatencyTrace(measured_at=T0),
    )
    wire = ready.model_dump_json()
    assert PLAYER_OUTBOUND.validate_json(wire) == ready
    assert CONSOLE_OUTBOUND.validate_json(wire) == ready


def test_a_creative_has_one_to_three_brands() -> None:
    slot = CreativeSlot(brand_id="b", brand_name="B", logo_url="/b.svg", copy_text="x")
    with pytest.raises(ValidationError):
        CreativeSpec(layout_id="l", slots=[], duration_ms=5000)
    with pytest.raises(ValidationError):
        CreativeSpec(layout_id="l", slots=[slot] * 4, duration_ms=5000)


def test_every_message_is_versioned_and_typed_uniquely() -> None:
    types = [m.model_fields["type"].default for m in ALL_MESSAGES]
    assert len(types) == len(set(types))
    for model in ALL_MESSAGES:
        assert model.model_fields["v"].default == 1, model.__name__


def test_socket_unions_only_reference_known_messages() -> None:
    for members in SOCKET_UNIONS.values():
        assert set(members) <= set(ALL_MESSAGES)


def test_contract_is_self_contained_so_the_agent_can_vendor_it() -> None:
    import app.contract.messages as module

    source = Path(module.__file__).read_text(encoding="utf-8")
    assert "from app" not in source and "import app" not in source


def test_minor_likely_bounds() -> None:
    with pytest.raises(ValidationError):
        MinorLikely(value=True, confidence=1.5)
