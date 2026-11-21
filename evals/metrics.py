"""Pure scoring: product KPIs, invariant oracles and latency percentiles.

Everything here is a function of recorded observations, so the same code
scores a scripted scenario, a replayed real-video recording and a live
rehearsal. The invariant oracles are deliberately independent of the auction
code: they re-derive "competitor", "age restricted" and "blank" from the wire
messages instead of trusting the engine's own flags.

KPI definitions follow docs/product.md §8.
"""

from __future__ import annotations

import itertools
import math
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal

from app.contract.messages import ConsoleAuction, CreativeReady, LatencyTrace

# Invariant 2: minor_likely only *blocks* these categories. Oracle list, kept
# here on purpose so a bug that drops the engine's age_restricted flag is caught.
AGE_RESTRICTED_CATEGORIES = frozenset({"alcohol", "gambling", "unhealthy_food", "tobacco"})

# Latency budgets (docs/architecture.md §3). Hard gates in the scorecard.
SERVER_DECISION_P95_MS = 30.0
LOOPBACK_ROUND_TRIP_P95_MS = 100.0
GLASS_TO_GLASS_P50_MS = 1500.0
GLASS_TO_GLASS_P95_MS = 2500.0


@dataclass(frozen=True, slots=True, kw_only=True)
class Decision:
    """One observed screen change: what the player got and what the console explained."""

    source: str  # scenario name, recording name or "rehearsal"
    step: int
    minor_likely: bool
    creative: CreativeReady
    auction: ConsoleAuction | None
    swapped: bool = True  # the screen changed because of this decision
    swap: str | None = None  # engine's swap action, when it reports one
    round_trip_ms: float | None = None  # agent send -> player receive, wall clock

    @property
    def brands(self) -> frozenset[str]:
        if self.creative.auction_id is None:
            return frozenset()
        return frozenset(slot.brand_name for slot in self.creative.spec.slots)

    @property
    def paid(self) -> bool:
        return self.creative.auction_id is not None

    @property
    def server_ms(self) -> float | None:
        """Receipt to creative ready on a swap; receipt to cleared on a non-swap decision."""
        if self.swapped:
            return span_ms(self.creative.trace, "received_at", "ready_at")
        if self.auction is None:
            return None
        return span_ms(self.auction.trace, "received_at", "cleared_at")

    @property
    def glass_to_glass_ms(self) -> float | None:
        if not self.swapped:
            return None
        return span_ms(
            self.creative.trace,
            "candidate_at" if self.creative.trace.candidate_at else "measured_at",
            "displayed_at",
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class Violation:
    kind: str
    source: str
    step: int
    detail: str


def span_ms(trace: LatencyTrace, start: str, end: str) -> float | None:
    begin, finish = getattr(trace, start), getattr(trace, end)
    if begin is None or finish is None:
        return None
    return (finish - begin).total_seconds() * 1000


def percentile(values: Sequence[float], q: float) -> float | None:
    """Linear-interpolated percentile (q in [0, 100]); None for no samples."""
    if not values:
        return None
    ordered = sorted(values)
    rank = (len(ordered) - 1) * q / 100
    low, high = math.floor(rank), math.ceil(rank)
    if low == high:
        return ordered[low]
    return ordered[low] + (ordered[high] - ordered[low]) * (rank - low)


def invariant_violations(decisions: Iterable[Decision]) -> list[Violation]:
    """Product invariants that must hold on every decision, demo or not."""
    violations: list[Violation] = []
    for d in decisions:
        slots = d.creative.spec.slots
        if not slots or any(not slot.logo_url for slot in slots):
            violations.append(
                Violation(
                    kind="blank_screen",
                    detail="creative without a slot or logo",
                    source=d.source,
                    step=d.step,
                )
            )
        if d.auction is None:
            continue
        category = {bid.campaign_id: bid.category for bid in d.auction.bids}
        winner_categories = [category.get(w.campaign_id, "?") for w in d.auction.winners]
        if len(set(winner_categories)) != len(winner_categories):
            violations.append(
                Violation(
                    kind="competitors_together",
                    detail=", ".join(winner_categories),
                    source=d.source,
                    step=d.step,
                )
            )
        if d.minor_likely and AGE_RESTRICTED_CATEGORIES.intersection(winner_categories):
            violations.append(
                Violation(
                    kind="minor_targeted",
                    detail=", ".join(winner_categories),
                    source=d.source,
                    step=d.step,
                )
            )
        violations.extend(
            Violation(kind=kind, detail=detail, source=d.source, step=d.step)
            for kind, detail in _match_violations(d.auction)
        )
        winners = frozenset(w.brand_name for w in d.auction.winners)
        # Only a swap must show the auction's winners; a kept incumbent or a
        # hold intentionally leaves the previous creative on air.
        if d.swapped and d.swap in (None, "swap", "released") and winners != d.brands:
            violations.append(
                Violation(
                    kind="screen_disagrees_with_auction",
                    detail=f"screen={sorted(d.brands)} auction={sorted(winners)}",
                    source=d.source,
                    step=d.step,
                )
            )
        if any(Decimal(w.price_per_as.amount) < 0 for w in d.auction.winners):
            violations.append(
                Violation(kind="negative_price", detail="", source=d.source, step=d.step)
            )
        if not d.swapped:
            continue
        instants = [
            getattr(d.creative.trace, name)
            for name in ("received_at", "moment_changed_at", "cleared_at", "ready_at")
        ]
        present = [instant for instant in instants if instant is not None]
        if present != sorted(present):
            violations.append(
                Violation(kind="trace_out_of_order", detail="", source=d.source, step=d.step)
            )
    return violations


def _match_violations(auction: ConsoleAuction) -> list[tuple[str, str]]:
    """Segment-affinity oracles, re-derived from the wire (auction affinity.py).

    - every bid reaches each moment segment once, an exact match keeps the whole
      bid and an approximate one strictly less;
    - an age-restricted category only ever matches exactly (affinity never widens
      its reach);
    - a solo winner (no basket bonus can apply) never clears above its best
      matched rate × live multiplier.
    """
    found: list[tuple[str, str]] = []
    lines = {bid.campaign_id: bid for bid in auction.bids}
    for bid in auction.bids:
        segments = [match.segment_id for match in bid.matches]
        if len(segments) != len(set(segments)):
            found.append(("bid_match_duplicate", f"{bid.brand_name}: {segments}"))
        for match in bid.matches:
            exact = match.segment_id == match.bid_segment_id
            consistent = (match.kind.value == "exact") == exact and (
                match.weight == 1 if exact else 0 < match.weight < 1
            )
            if not consistent:
                found.append(
                    (
                        "bid_match_inconsistent",
                        f"{bid.brand_name}: {match.kind.value} {match.bid_segment_id}"
                        f"->{match.segment_id} @ {match.weight}",
                    )
                )
            if bid.category in AGE_RESTRICTED_CATEGORIES and not exact:
                found.append(("restricted_reach_widened", f"{bid.brand_name}: {match.kind.value}"))
    if len(auction.winners) == 1:
        (winner,) = auction.winners
        line = lines.get(winner.campaign_id)
        if line is not None and line.matches:
            ceiling = Decimal(line.bid_per_as.amount) * Decimal(line.multiplier or "1")
            if Decimal(winner.price_per_as.amount) > ceiling + Decimal("0.0001"):
                found.append(
                    (
                        "charged_above_matched_bid",
                        f"{winner.brand_name}: {winner.price_per_as.amount} > {ceiling}",
                    )
                )
    return found


def product_kpis(decisions: Sequence[Decision]) -> dict[str, float | int | None]:
    """docs/product.md §8, computed over observed decisions."""
    auctions = [d.auction for d in decisions if d.auction is not None]
    filled = [a for a in auctions if a.winners]
    baskets = [a for a in filled if len(a.winners) >= 2]
    lifts = [
        float(Decimal(a.bundle_value.amount) / Decimal(a.single_brand_value.amount) - 1)
        for a in baskets
        if Decimal(a.single_brand_value.amount) > 0
    ]
    # Revenue in one unit (money per screen-second): what the basket clears for
    # versus what the best single brand would have paid for the same moments.
    revenue = [
        (Decimal(a.bundle_revenue.amount), Decimal(a.single_brand_revenue.amount))
        for a in filled
        if a.bundle_revenue is not None and a.single_brand_revenue is not None
    ]
    cleared = sum((basket for basket, _ in revenue), Decimal(0))
    solo_revenue = sum((solo for _, solo in revenue), Decimal(0))
    exclusions = Counter(e.reason.value for a in auctions for e in a.exclusions)
    bid_lines = [bid for a in auctions for bid in a.bids]
    approximate = [b for b in bid_lines if any(m.kind.value != "exact" for m in b.matches)]
    affinity_only = [
        b for b in bid_lines if b.matches and all(m.kind.value != "exact" for m in b.matches)
    ]
    return {
        "decisions": len(decisions),
        "fill_rate": _ratio(len(filled), len(auctions)),
        "basket_rate": _ratio(len(baskets), len(filled)),
        "mean_coverage": _mean([a.coverage for a in filled]),
        "basket_value_lift": _mean(lifts),
        "basket_revenue_lift": float(cleared / solo_revenue - 1) if solo_revenue else None,
        "brands_per_paid_play": _mean([len(a.winners) for a in filled]),
        "exclusions_competitor": exclusions.get("competitor", 0),
        "exclusions_safety": exclusions.get("safety", 0),
        "exclusions_below_reserve": exclusions.get("below_reserve", 0),
        "affinity_bid_share": _ratio(len(approximate), len(bid_lines)),
        "affinity_only_bids": len(affinity_only),
        "decisions_swapped": sum(d.swapped for d in decisions),
        "decisions_kept_on_air": sum(not d.swapped for d in decisions),
    }


def latency_kpis(decisions: Sequence[Decision]) -> dict[str, float | int | None]:
    server = [ms for d in decisions if (ms := d.server_ms) is not None]
    rtt = [d.round_trip_ms for d in decisions if d.round_trip_ms is not None]
    g2g = [ms for d in decisions if (ms := d.glass_to_glass_ms) is not None]
    return {
        "server_decision_p50_ms": percentile(server, 50),
        "server_decision_p95_ms": percentile(server, 95),
        "server_decision_max_ms": max(server, default=None),
        "loopback_round_trip_p50_ms": percentile(rtt, 50),
        "loopback_round_trip_p95_ms": percentile(rtt, 95),
        "loopback_round_trip_max_ms": max(rtt, default=None),
        "glass_to_glass_p50_ms": percentile(g2g, 50),
        "glass_to_glass_p95_ms": percentile(g2g, 95),
        "glass_to_glass_samples": len(g2g),
    }


def flicker_per_minute(decisions: Sequence[Decision], *, duration_s: float) -> float | None:
    """Screen changes per minute that swapped the paid brand set. Lower is calmer."""
    if duration_s <= 0:
        return None
    swaps = sum(1 for prev, cur in itertools.pairwise(decisions) if prev.brands != cur.brands)
    return swaps * 60 / duration_s


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _mean(values: Sequence[float | int]) -> float | None:
    return sum(values) / len(values) if values else None
