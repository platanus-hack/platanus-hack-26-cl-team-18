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
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from agent.vision.tracking import TrackView
from agent.vision.types import MarkerDetection

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

    A product counts as held when it was seen near a person in at least
    `min_hits` of the last `window` frames. Markers far from every person (a
    can left on the counter) are ignored: on a shelf is not in a hand.
    """

    def __init__(
        self,
        catalog: MarkerCatalog,
        *,
        window: int = 8,
        min_hits: int = 3,
        person_margin: float = 0.25,
    ) -> None:
        if not 0 < min_hits <= window:
            raise ValueError("need 0 < min_hits <= window")
        self._catalog = catalog
        self._window: deque[frozenset[str]] = deque(maxlen=window)
        self._min_hits = min_hits
        self._margin = person_margin

    def reset(self) -> None:
        self._window.clear()

    def update(
        self, markers: tuple[MarkerDetection, ...], tracks: list[TrackView]
    ) -> list[HeldProductView]:
        seen: set[str] = set()
        reach = [t.box.expanded(self._margin) for t in tracks]
        for marker in markers:
            brand = self._catalog.brand_for(marker.marker_id)
            if brand is None:
                continue
            if any(area.contains(marker.box.center) for area in reach):
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
