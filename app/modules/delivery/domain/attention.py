"""Attention-seconds measured during a play (the billable unit).

An attention-second is one looking person for one second while a creative
plays (CONTEXT.md). Looks come from the agent's anonymous tracklets, sampled
per snapshot and held until the next snapshot (the agent sends on change).

D7, minimum billable look: a tracklet's look counts only when it lasts at
least `MIN_BILLABLE_LOOK` *within the play window*, and then counts in full.
Shorter glances bill nothing. A look already in progress before the play
started counts only from the start of the play, so a glance at the previous ad
is never billed to this one.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_DOWN, Decimal

MIN_BILLABLE_LOOK = timedelta(seconds=1)
# A sample is trusted this long without a newer one (agent silent: stop counting).
MAX_SAMPLE_HOLD = timedelta(seconds=10)
_MS = Decimal("0.001")


@dataclass(frozen=True, slots=True, kw_only=True)
class LookSample:
    """Which anonymous, session-local tracklets were looking at `at` (device time)."""

    at: datetime
    looking: frozenset[str]


@dataclass(frozen=True, slots=True, kw_only=True)
class Attention:
    attention_seconds: Decimal  # 3 dp, rounded down: never over-bill
    billable_looks: int
    glances: int  # looks shorter than the minimum: not billed


def seconds_of(duration: timedelta) -> Decimal:
    """Exact seconds of a timedelta (no float round trip)."""
    whole = Decimal(duration.days * 86_400 + duration.seconds)
    return whole + Decimal(duration.microseconds) / 1_000_000


def measure_attention(
    samples: Sequence[LookSample],
    *,
    start: datetime,
    end: datetime,
    min_look: timedelta = MIN_BILLABLE_LOOK,
    max_hold: timedelta = MAX_SAMPLE_HOLD,
) -> Attention:
    if end <= start:
        return Attention(attention_seconds=Decimal(0), billable_looks=0, glances=0)
    ordered = sorted(samples, key=lambda sample: sample.at)
    before = [sample for sample in ordered if sample.at <= start]
    relevant = ([before[-1]] if before else []) + [
        sample for sample in ordered if start < sample.at < end
    ]
    runs: dict[str, list[tuple[datetime, datetime]]] = {}
    for index, sample in enumerate(relevant):
        following = relevant[index + 1].at if index + 1 < len(relevant) else end
        seg_start = max(sample.at, start)
        seg_end = min(following, sample.at + max_hold, end)
        if seg_end <= seg_start:
            continue
        for tracklet in sample.looking:
            spans = runs.setdefault(tracklet, [])
            if spans and spans[-1][1] >= seg_start:
                spans[-1] = (spans[-1][0], seg_end)  # the same look continues
            else:
                spans.append((seg_start, seg_end))
    total = timedelta()
    billable = glances = 0
    for spans in runs.values():
        for span_start, span_end in spans:
            duration = span_end - span_start
            if duration >= min_look:
                total += duration
                billable += 1
            else:
                glances += 1
    return Attention(
        attention_seconds=seconds_of(total).quantize(_MS, rounding=ROUND_DOWN),
        billable_looks=billable,
        glances=glances,
    )
