"""When a keyframe is worth a paid image-model call: the VLM as a tie-breaker.

Real footage showed YOLO's classes are too coarse where it matters most: a beer bottle and
a gym bottle are both "bottle", a beer glass is a "cup". Calling the VLM on every keyframe
(one per 1.5 s) pays for many answers the hot path already has. Tag a keyframe when:

* an ambiguous object is in view (the VLM breaks the tie: beer or energy, coffee or beer);
* people look but nothing is understood (a queue: maybe the VLM sees what YOLO can't);
* otherwise at most every `refresh`, below the VLM evidence TTL so tagged evidence never
  lapses, and so the VLM stays the second line for minor safety (invariant 2): it is the
  only check that sees a carried toddler, and it is never silenced for long.

Nobody looking: refresh cadence only. There is nobody to sell to, but a child near the
screen matters whether or not they look at it, so the safety refresh never stops.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

AMBIGUOUS_OBJECTS = frozenset({"bottle", "cup", "wine glass", "can"})


@dataclass(frozen=True, slots=True, kw_only=True)
class KeyframeGate:
    refresh: timedelta = timedelta(seconds=5)
    ambiguous: frozenset[str] = AMBIGUOUS_OBJECTS

    def should_tag(
        self,
        *,
        looking: int,
        objects: frozenset[str],
        understood: bool,
        last_tagged_at: datetime | None,
        now: datetime,
    ) -> str | None:
        """The reason to tag now, or None to skip this keyframe."""
        if looking > 0 and objects & self.ambiguous:
            return "ambiguous_object"
        if looking > 0 and not understood:
            return "nothing_understood"
        if last_tagged_at is None or now - last_tagged_at >= self.refresh:
            return "refresh"
        return None
