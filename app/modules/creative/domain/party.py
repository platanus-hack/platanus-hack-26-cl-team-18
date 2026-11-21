"""How many people an offer addresses: the group, not every glance (auction-plan D5.5).

On real footage the looking count jitters as people in conversation glance at and away
from the screen (1 → 3 → 4 → 3 → 2 → 3 in ten seconds of a five-person party). Sizing
the offer to each raw count rebuilt the creative every second ("los dos" ↔ "los tres").
The addressed party is the largest count *confirmed* within the grace window: seen in at
least two samples, so a one-frame glance-in by someone already there never resizes the
offer. Someone *arriving* (more people present than at any earlier sample in the window)
upsizes it at once: that is the third friend walking up to the counter. A smaller group takes over
only once the bigger count has not been confirmed for the whole window. Billing still
uses the raw count (attention-seconds).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

GROUP_WINDOW = timedelta(seconds=3)  # = SwapTimings.leave_grace: one notion of "a blink"
CONFIRM_SAMPLES = 2  # a count seen once is a glance; seen twice, someone is there


@dataclass(frozen=True, slots=True)
class LookingSample:
    measured_at: datetime
    looking: int
    people: int = 0  # present, looking or not: an arrival raises it, a glance doesn't


def addressed_party(
    samples: Iterable[LookingSample],
    *,
    now: datetime,
    window: timedelta = GROUP_WINDOW,
    confirm: int = CONFIRM_SAMPLES,
) -> int:
    """The largest count seen in ≥ `confirm` samples in (now - window, now].

    With no confirmed count (a brand-new group), the latest sample's count: the first
    frame of a group gets its offer at once.
    """
    recent = sorted(
        (s for s in samples if now - window < s.measured_at <= now), key=lambda s: s.measured_at
    )
    if not recent:
        return 0
    latest = recent[-1]
    counts = sorted((s.looking for s in recent), reverse=True)
    confirmed = counts[confirm - 1] if len(counts) >= confirm else latest.looking
    earlier_people = max((s.people for s in recent[:-1]), default=0)
    if latest.people > earlier_people:  # an arrival, not a glance: size up now
        return max(confirmed, latest.looking)
    return confirmed
