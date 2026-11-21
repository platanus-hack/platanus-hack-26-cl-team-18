from __future__ import annotations

from dataclasses import dataclass
from typing import Self

from app.modules.audience.domain.value_objects.evidence import HeldProduct
from app.modules.audience.domain.value_objects.ids import MomentId
from app.modules.audience.domain.value_objects.segment import SegmentEstimate
from app.modules.common.domain.primitives.value_object import ValueObject
from app.modules.common.domain.value_objects.utc_timestamp import UtcTimestamp
from app.modules.screens.domain.value_objects.ids import ScreenId


@dataclass(frozen=True, slots=True, kw_only=True)
class AudienceMomentSnapshot(ValueObject):
    """Published audience facts for an auction at the time a moment changed."""

    screen_id: ScreenId
    moment_id: MomentId
    measured_at: UtcTimestamp
    received_at: UtcTimestamp
    moment_changed_at: UtcTimestamp
    people: int
    looking: int
    segments: tuple[SegmentEstimate, ...]
    minor_likely: bool
    held_products: tuple[HeldProduct, ...] = ()

    def __post_init__(self) -> None:
        self._enforce_types()

    @classmethod
    def create(
        cls,
        *,
        screen_id: ScreenId,
        moment_id: MomentId,
        measured_at: UtcTimestamp,
        received_at: UtcTimestamp,
        moment_changed_at: UtcTimestamp,
        people: int,
        looking: int,
        segments: tuple[SegmentEstimate, ...],
        minor_likely: bool,
        held_products: tuple[HeldProduct, ...] = (),
    ) -> Self:
        return cls(
            screen_id=screen_id,
            moment_id=moment_id,
            measured_at=measured_at,
            received_at=received_at,
            moment_changed_at=moment_changed_at,
            people=people,
            looking=looking,
            segments=segments,
            minor_likely=minor_likely,
            held_products=held_products,
        )

    def _enforce_types(self) -> None:
        if not isinstance(self.screen_id, ScreenId) or not isinstance(self.moment_id, MomentId):
            raise TypeError("screen_id and moment_id must use their respective typed IDs")
        if any(
            not isinstance(instant, UtcTimestamp)
            for instant in (self.measured_at, self.received_at, self.moment_changed_at)
        ):
            raise TypeError("audience moment instants must be UtcTimestamp values")
        if self.moment_changed_at.value < self.received_at.value:
            raise ValueError("moment_changed_at cannot precede received_at")
        if type(self.people) is not int or type(self.looking) is not int:
            raise TypeError("people and looking must be ints")
        if not 0 <= self.looking <= self.people:
            raise ValueError("counts must satisfy 0 <= looking <= people")
        if type(self.minor_likely) is not bool:
            raise TypeError("minor_likely must be a bool")
        if not isinstance(self.segments, tuple) or any(
            not isinstance(segment, SegmentEstimate) for segment in self.segments
        ):
            raise TypeError("segments must be a tuple of SegmentEstimate values")
        if any(segment.viewer_count > self.looking for segment in self.segments):
            raise ValueError("segment viewer counts cannot exceed looking")
        if len({segment.segment_id for segment in self.segments}) != len(self.segments):
            raise ValueError("segment IDs must be unique within a moment")
        if not isinstance(self.held_products, tuple) or any(
            not isinstance(product, HeldProduct) for product in self.held_products
        ):
            raise TypeError("held_products must be a tuple of HeldProduct values")
