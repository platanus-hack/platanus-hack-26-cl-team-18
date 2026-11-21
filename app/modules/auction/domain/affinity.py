"""Which moment segments a segment bid reaches, and how much of the bid each one keeps.

A bid is on one IAB segment, but the moment may tag the same need with a
neighbouring one: a "Sports" bid should reach a cyclist, and a "Coffee" bid a
ready-to-drink coffee buyer. The table answers that with a fixed, reviewable
lookup instead of a text-similarity call, so the auction stays pure, fast and
explainable (docs/architecture.md §6 step 2, "Segment matching").

    exact     bid segment == moment segment                         weight 1
    broader   the bid is an ancestor of the moment segment          BROADER_WEIGHT ** levels
    narrower  the bid is a descendant of the moment segment         NARROWER_WEIGHT ** levels
    related   a curated pair from the catalog (`related`)           its own weight

The hierarchy comes from the catalog's IAB paths. Related pairs are symmetric,
never transitive, and a pair already linked by the hierarchy keeps the better
weight. A weight scales both the bid's value and its charge ceiling: an
approximate match never costs a brand more than its discounted bid.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum

from app.modules.audience.domain.value_objects.segment import SegmentId

ZERO = Decimal(0)
ONE = Decimal(1)
# A "Sports" bid reaches a cyclist almost as well as a "Cycling" bid would.
BROADER_WEIGHT = Decimal("0.8")
# A "Cycling" bid reaches only some of a "Sports" crowd.
NARROWER_WEIGHT = Decimal("0.4")
_PATH_SEPARATOR = "|"


class MatchKind(StrEnum):
    EXACT = "exact"
    BROADER = "broader"
    NARROWER = "narrower"
    RELATED = "related"


# Ties on weight prefer the more literal match, so the console reason is stable.
_KIND_RANK = {MatchKind.EXACT: 0, MatchKind.BROADER: 1, MatchKind.NARROWER: 2, MatchKind.RELATED: 3}


@dataclass(frozen=True, slots=True, kw_only=True)
class SegmentLink:
    """A bid on `bid_segment` reaches viewers in `moment_segment` at `weight` of its rate."""

    bid_segment: SegmentId
    moment_segment: SegmentId
    kind: MatchKind
    weight: Decimal

    def __post_init__(self) -> None:
        if not self.weight.is_finite() or not ZERO < self.weight <= ONE:
            raise ValueError("A segment link weight must be in (0, 1]")
        exact = self.bid_segment == self.moment_segment
        if exact != (self.kind is MatchKind.EXACT):
            raise ValueError("Only a segment linked to itself is an exact match")
        if exact and self.weight != ONE:
            raise ValueError("An exact match keeps the whole bid")

    def outranks(self, other: SegmentLink) -> bool:
        return (self.weight, -_KIND_RANK[self.kind]) > (other.weight, -_KIND_RANK[other.kind])


@dataclass(frozen=True, slots=True, kw_only=True)
class RelatedSegments:
    """A curated, symmetric pair: viewers of either are prospects for the other."""

    left: SegmentId
    right: SegmentId
    weight: Decimal

    def __post_init__(self) -> None:
        if self.left == self.right:
            raise ValueError("A segment is not related to itself")
        if not self.weight.is_finite() or not ZERO < self.weight < ONE:
            raise ValueError("A related-segment weight must be in (0, 1)")


@dataclass(frozen=True, slots=True)
class SegmentAffinity:
    """The approximate-match table. `exact_only()` reproduces literal segment matching."""

    links: tuple[SegmentLink, ...] = ()
    _by_bid: dict[SegmentId, tuple[SegmentLink, ...]] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        by_bid: dict[SegmentId, dict[SegmentId, SegmentLink]] = {}
        for link in self.links:
            if link.kind is MatchKind.EXACT:
                raise ValueError("Exact matches are implicit; list only approximate links")
            targets = by_bid.setdefault(link.bid_segment, {})
            if link.moment_segment in targets:
                raise ValueError("Each (bid, moment) segment pair is linked at most once")
            targets[link.moment_segment] = link
        object.__setattr__(
            self,
            "_by_bid",
            {
                bid: tuple(sorted(targets.values(), key=lambda item: item.moment_segment.value))
                for bid, targets in by_bid.items()
            },
        )

    @classmethod
    def exact_only(cls) -> SegmentAffinity:
        return cls()

    @classmethod
    def build(
        cls,
        *,
        paths: Mapping[SegmentId, str],
        related: Iterable[RelatedSegments] = (),
        broader_weight: Decimal = BROADER_WEIGHT,
        narrower_weight: Decimal = NARROWER_WEIGHT,
    ) -> SegmentAffinity:
        """Derive hierarchy links from IAB `paths` and add the curated `related` pairs."""
        best: dict[tuple[SegmentId, SegmentId], SegmentLink] = {}

        def offer(link: SegmentLink) -> None:
            key = (link.bid_segment, link.moment_segment)
            current = best.get(key)
            if current is None or link.outranks(current):
                best[key] = link

        parts = {segment: _parts(path) for segment, path in paths.items()}
        for ancestor, upper in parts.items():
            for descendant, lower in parts.items():
                levels = len(lower) - len(upper)
                if levels <= 0 or lower[: len(upper)] != upper:
                    continue
                offer(
                    SegmentLink(
                        bid_segment=ancestor,
                        moment_segment=descendant,
                        kind=MatchKind.BROADER,
                        weight=broader_weight**levels,
                    )
                )
                offer(
                    SegmentLink(
                        bid_segment=descendant,
                        moment_segment=ancestor,
                        kind=MatchKind.NARROWER,
                        weight=narrower_weight**levels,
                    )
                )
        for pair in related:
            for bid, seen in ((pair.left, pair.right), (pair.right, pair.left)):
                offer(
                    SegmentLink(
                        bid_segment=bid,
                        moment_segment=seen,
                        kind=MatchKind.RELATED,
                        weight=pair.weight,
                    )
                )
        ordered = sorted(
            best.values(), key=lambda item: (item.bid_segment.value, item.moment_segment.value)
        )
        return cls(links=tuple(ordered))

    def reach(self, bid_segment: SegmentId) -> tuple[SegmentLink, ...]:
        """Every moment segment a bid on `bid_segment` reaches, the exact one first."""
        exact = SegmentLink(
            bid_segment=bid_segment, moment_segment=bid_segment, kind=MatchKind.EXACT, weight=ONE
        )
        return (exact, *self._by_bid.get(bid_segment, ()))


def _parts(path: str) -> tuple[str, ...]:
    return tuple(part.strip().casefold() for part in path.split(_PATH_SEPARATOR) if part.strip())
