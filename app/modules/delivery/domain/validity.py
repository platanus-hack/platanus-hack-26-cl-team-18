"""Invalid-attention filtering: the in-store equivalent of invalid-traffic filtering.

Attention-seconds are what brands pay for, so every second must be a real person
plausibly seeing the ad. Pure rules, each with a reason the receipt shows:

- **glance**: a look shorter than the minimum within the play (D7) bills nothing.
- **far**: a viewer in the "far" distance band can't read the ad.
- **static**: one uninterrupted look longer than `max_static_look` is a poster, a
  mannequin, a screen in the frame or a stuck track, not a shopper. It is dropped
  entirely, not capped: we can't tell where the real part ended.
- **over_capacity**: more simultaneous lookers than the screen plausibly serves
  (tracker id churn, a crowd on a projected video). The excess is dropped.
- **duplicate_sample**: a replayed or repeated reading (same instant) is ignored,
  so a looping agent or a replayed feed can't double-count.

Billable looks count in full; filtered seconds are reported per reason.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import ROUND_DOWN, Decimal
from enum import StrEnum

from app.modules.delivery.domain.attention import (
    MAX_SAMPLE_HOLD,
    MIN_BILLABLE_LOOK,
    LookSample,
    seconds_of,
)

_MS = Decimal("0.001")


class InvalidReason(StrEnum):
    GLANCE = "glance"
    FAR = "far"
    STATIC = "static"
    OVER_CAPACITY = "over_capacity"
    DUPLICATE_SAMPLE = "duplicate_sample"


@dataclass(frozen=True, slots=True, kw_only=True)
class ValidityRules:
    min_look: timedelta = MIN_BILLABLE_LOOK
    max_hold: timedelta = MAX_SAMPLE_HOLD
    max_static_look: timedelta = timedelta(minutes=3)
    max_viewers: int = 12  # a counter screen; a venue setting in production


@dataclass(frozen=True, slots=True, kw_only=True)
class ValidatedAttention:
    attention_seconds: Decimal  # billable, 3 dp, rounded down
    billable_looks: int
    glances: int
    invalid_seconds: dict[InvalidReason, Decimal] = field(default_factory=dict)
    duplicate_samples: int = 0

    @property
    def filtered_seconds(self) -> Decimal:
        return sum(self.invalid_seconds.values(), Decimal(0))


def validate_attention(
    samples: Sequence[LookSample],
    *,
    start: datetime,
    end: datetime,
    rules: ValidityRules | None = None,
) -> ValidatedAttention:
    rules = rules or ValidityRules()
    if end <= start:
        return ValidatedAttention(attention_seconds=Decimal(0), billable_looks=0, glances=0)
    # duplicate_sample: one reading per instant; the first one wins.
    seen: set[datetime] = set()
    ordered: list[LookSample] = []
    duplicates = 0
    for sample in sorted(samples, key=lambda item: item.at):
        if sample.at in seen:
            duplicates += 1
            continue
        seen.add(sample.at)
        ordered.append(sample)
    before = [sample for sample in ordered if sample.at <= start]
    relevant = ([before[-1]] if before else []) + [s for s in ordered if start < s.at < end]

    invalid: dict[InvalidReason, timedelta] = {}

    def drop(reason: InvalidReason, duration: timedelta) -> None:
        invalid[reason] = invalid.get(reason, timedelta()) + duration

    runs: dict[str, list[tuple[datetime, datetime]]] = {}
    for index, sample in enumerate(relevant):
        following = relevant[index + 1].at if index + 1 < len(relevant) else end
        seg_start = max(sample.at, start)
        seg_end = min(following, sample.at + rules.max_hold, end)
        if seg_end <= seg_start:
            continue
        span = seg_end - seg_start
        near = sorted(sample.looking - sample.far)
        for _ in sample.looking & sample.far:
            drop(InvalidReason.FAR, span)
        kept, excess = near[: rules.max_viewers], near[rules.max_viewers :]
        for _ in excess:
            drop(InvalidReason.OVER_CAPACITY, span)
        for tracklet in kept:
            spans = runs.setdefault(tracklet, [])
            if spans and spans[-1][1] >= seg_start:
                spans[-1] = (spans[-1][0], seg_end)
            else:
                spans.append((seg_start, seg_end))

    total = timedelta()
    billable = glances = 0
    for spans in runs.values():
        for span_start, span_end in spans:
            duration = span_end - span_start
            if duration < rules.min_look:
                glances += 1
                drop(InvalidReason.GLANCE, duration)
            elif duration > rules.max_static_look:
                drop(InvalidReason.STATIC, duration)
            else:
                total += duration
                billable += 1
    return ValidatedAttention(
        attention_seconds=seconds_of(total).quantize(_MS, rounding=ROUND_DOWN),
        billable_looks=billable,
        glances=glances,
        invalid_seconds={
            reason: seconds_of(duration).quantize(_MS, rounding=ROUND_DOWN)
            for reason, duration in sorted(invalid.items())
        },
        duplicate_samples=duplicates,
    )
