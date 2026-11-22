"""Held products from printed markers ("complete the basket", docs/demo.md §3 beat 3).

Each demo prop (a Café Patio cup, a Voltio can, …) carries a small ArUco
marker. Detection is a few milliseconds in the hot path and deterministic,
which a label-reading VLM in a hall full of lights is not. The VLM path can
still run in parallel on the warm keyframe; this module is the reliable one.

Objects only: a marker says *which product* is in the frame and that a person
box is near it. It never says anything about the person.
"""

from __future__ import annotations

import json
import math
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from agent.vision.tracking import TrackView
from agent.vision.types import Box, Kp, MarkerDetection, PersonDetection

# Wire convention until the contract carries `held_products` (see edge/README.md):
# a held product travels as a DetectedObject labelled "product:<brand_id>".
HELD_LABEL_PREFIX = "product:"


@dataclass(frozen=True, slots=True)
class MarkerCatalog:
    """Marker id → brand id. Lives in a JSON file next to the printed props."""

    brands: dict[int, str]

    @classmethod
    def from_file(cls, path: Path) -> MarkerCatalog:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls(brands={int(k): str(v) for k, v in raw.items()})

    def brand_for(self, marker_id: int) -> str | None:
        return self.brands.get(marker_id)


@dataclass(frozen=True, slots=True)
class HeldProductView:
    brand_id: str
    confidence: float  # share of recent frames the product was seen held


class HeldProductTracker:
    """Debounces markers over a short window so a prop doesn't flicker in and out.

    A product counts as held when it was seen in a hand in at least `min_hits`
    of the last `window` frames. "In a hand" means near a visible wrist of a
    tracked person: within `hand_reach` shoulder widths (or two marker sides,
    whichever is larger). On real footage a held cup's box sits 0–0.42 shoulder
    widths from the nearest wrist; 0.75 leaves room for the marker on the cup's side. A cup on the demo table is next to people, not in
    their hands; inside a passer-by's padded box it used to count as held and
    fired "complete the basket" (found in the crowded-hall audit). A person
    whose pose has no visible wrist holds nothing. Without pose at all (a
    detector that returns no keypoints) the old rule applies: within the
    person box padded by `person_margin`.
    """

    def __init__(
        self,
        catalog: MarkerCatalog,
        *,
        window: int = 8,
        min_hits: int = 3,
        person_margin: float = 0.25,
        hand_reach: float = 0.75,
        wrist_min_confidence: float = 0.2,
    ) -> None:
        if not 0 < min_hits <= window:
            raise ValueError("need 0 < min_hits <= window")
        self._catalog = catalog
        self._window: deque[frozenset[str]] = deque(maxlen=window)
        self._min_hits = min_hits
        self._margin = person_margin
        self._hand_reach = hand_reach
        self._wrist_conf = wrist_min_confidence

    def reset(self) -> None:
        self._window.clear()

    def update(
        self,
        markers: tuple[MarkerDetection, ...],
        tracks: list[TrackView],
        persons: Sequence[PersonDetection] | None = None,
    ) -> list[HeldProductView]:
        """Feed one frame. `persons` are the tracked people's current pose detections;
        None (no pose available) falls back to the padded track boxes."""
        seen: set[str] = set()
        for marker in markers:
            brand = self._catalog.brand_for(marker.marker_id)
            if brand is None:
                continue
            if persons is None:
                held = any(t.box.expanded(self._margin).contains(marker.box.center) for t in tracks)
            else:
                held = any(self._in_hand(marker.box, person) for person in persons)
            if held:
                seen.add(brand)
        self._window.append(frozenset(seen))
        counts: dict[str, int] = {}
        for frame in self._window:
            for brand in frame:
                counts[brand] = counts.get(brand, 0) + 1
        size = len(self._window)
        return [
            HeldProductView(brand_id=brand, confidence=round(hits / size, 2))
            for brand, hits in sorted(counts.items())
            if hits >= self._min_hits
        ]

    def _in_hand(self, marker: Box, person: PersonDetection) -> bool:
        if len(person.keypoints) <= Kp.RIGHT_WRIST:
            # No pose from this detector: near the person box is the best evidence.
            return person.box.expanded(self._margin).contains(marker.center)
        wrists = [
            point
            for index in (Kp.LEFT_WRIST, Kp.RIGHT_WRIST)
            if (point := person.kp(index, self._wrist_conf)) is not None
        ]
        if not wrists:
            return False
        left = person.kp(Kp.LEFT_SHOULDER, 0.5)
        right = person.kp(Kp.RIGHT_SHOULDER, 0.5)
        shoulders = abs(left.x - right.x) if left is not None and right is not None else 0.0
        # A turned body has narrow shoulders; half the box width is a floor.
        scale = max(shoulders, person.box.width * 0.5)
        reach = max(self._hand_reach * scale, 2 * max(marker.width, marker.height))
        return any(_distance_to_box(w.x, w.y, marker) <= reach for w in wrists)


def _distance_to_box(x: float, y: float, box: Box) -> float:
    dx = max(box.x1 - x, 0.0, x - box.x2)
    dy = max(box.y1 - y, 0.0, y - box.y2)
    return math.hypot(dx, dy)
