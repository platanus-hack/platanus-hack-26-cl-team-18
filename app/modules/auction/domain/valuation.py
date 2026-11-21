"""What one bidder's offer is worth for this moment, in money per screen-second.

    v_c = min( Σ_s rate_c(s) · viewers(s) · confidence(s) · speech_boost(s),
               max_s rate_c(s) · speech_boost(s) · looking )
          · live_mult_c · context_mult(category_c) · held_partner_boost_c

    rate_c(s) = max over the bidder's bids b reaching s of  bid_b · affinity(b → s)

Each moment segment counts once per bidder, through its best-paying bid: an
exact bid keeps its whole rate, a broader, narrower or related one a discount
(`SegmentAffinity`). The cap keeps overlapping segments from counting one
viewer twice.

Boosts (speech intent, context, held product) are the platform's relevance
signals: they raise a bid's *value*, so it ranks higher, but never what the
brand can be *charged*. A segment bid is a contractual maximum price per
attention-second, and an approximate match lowers that maximum by its weight.
Only the advertiser's own live multiplier and its opted-in basket bonuses
raise that ceiling (`price_ceiling`).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.modules.auction.domain.affinity import MatchKind, SegmentAffinity, SegmentLink
from app.modules.auction.domain.opportunity import ONE, ZERO, AdOpportunity
from app.modules.audience.domain.value_objects.segment import SegmentId
from app.modules.campaigns.domain.book import Bidder, BrandId

HUNDRED = Decimal(100)
# Someone *said* it: boost up to +50 % at full intent (R-C7). Capped by the
# per-bidder viewer cap above, so one loud person can't outweigh the group.
SPEECH_INTENT_ALPHA = Decimal("0.5")
# A held product boosts its approved partners up to +50 % (complete the basket, R-C6).
HELD_PARTNER_BOOST = Decimal("0.5")
_EXACT_ONLY = SegmentAffinity.exact_only()
_KIND_ORDER = (MatchKind.EXACT, MatchKind.BROADER, MatchKind.NARROWER, MatchKind.RELATED)


@dataclass(frozen=True, slots=True, kw_only=True)
class SegmentMatch:
    """How one moment segment was reached: through which bid, and at what discount."""

    moment_segment: SegmentId
    bid_segment: SegmentId
    kind: MatchKind
    weight: Decimal  # affinity weight in (0, 1]; 1 for an exact match
    rate: Decimal  # the bid's rate × weight, per attention-second, before boosts


@dataclass(frozen=True, slots=True, kw_only=True)
class Valuation:
    value: Decimal  # money per screen-second, boosts included (ranking)
    ceiling_rate: Decimal  # max chargeable per AS: best matched rate × live multiplier
    matches: tuple[SegmentMatch, ...] = ()  # one per reached moment segment, by segment id

    @property
    def matched(self) -> bool:
        """Bids on at least one segment present in the moment, exactly or by affinity."""
        return bool(self.matches)


def match_segments(
    bidder: Bidder, present: frozenset[SegmentId], affinity: SegmentAffinity
) -> tuple[SegmentMatch, ...]:
    """The best-paying way each present segment is reached by this bidder's bids.

    Age-restricted campaigns always match exactly: affinity never widens who an
    alcohol or gambling ad reaches, whatever the catalog's table says.
    """
    broad = bidder.broad_match and not bidder.age_restricted
    table = affinity if broad else _EXACT_ONLY
    best: dict[SegmentId, tuple[tuple[Decimal, int, tuple[int, ...]], SegmentMatch]] = {}
    for bid in bidder.segment_bids:
        for link in table.reach(bid.segment_id):
            if link.moment_segment not in present:
                continue
            match = _match(link, bid.rate.amount)
            # Highest rate first; ties prefer the more literal match, then a stable id.
            key = (match.rate, -_KIND_ORDER.index(match.kind), _invert(bid.segment_id.value))
            current = best.get(link.moment_segment)
            if current is None or key > current[0]:
                best[link.moment_segment] = (key, match)
    return tuple(best[segment][1] for segment in sorted(best, key=lambda item: item.value))


def reach_weights(
    bidder: Bidder, present: frozenset[SegmentId], affinity: SegmentAffinity
) -> dict[SegmentId, Decimal]:
    """How fully this bidder targets each present segment: its strongest route's weight.

    Pricing follows the best-*paying* route (`match_segments`), but reach is about
    whether the campaign wants these viewers at all: a cheap exact bid on a segment
    reaches it fully even when a pricier broader bid sets the rate.
    """
    table = affinity if bidder.broad_match and not bidder.age_restricted else _EXACT_ONLY
    reach: dict[SegmentId, Decimal] = {}
    for bid in bidder.segment_bids:
        for link in table.reach(bid.segment_id):
            if link.moment_segment in present:
                reach[link.moment_segment] = max(reach.get(link.moment_segment, ZERO), link.weight)
    return reach


def valuate(bidder: Bidder, opportunity: AdOpportunity) -> Valuation:
    moment = opportunity.moment
    segments = {segment.segment_id: segment for segment in moment.segments}
    matches = match_segments(bidder, frozenset(segments), opportunity.affinity)
    total = ZERO
    max_rate = ZERO
    max_bid = ZERO
    for match in matches:
        segment = segments[match.moment_segment]
        boost = ONE
        if segment.speech_intent is not None:
            boost += SPEECH_INTENT_ALPHA * segment.speech_intent.value / HUNDRED
        rate = match.rate * boost
        total += rate * segment.viewer_count * segment.confidence.value / HUNDRED
        max_rate = max(max_rate, rate)
        max_bid = max(max_bid, match.rate)
    total = min(total, max_rate * moment.looking)
    multiplier = (
        bidder.live_multiplier.value
        * opportunity.multiplier_for(bidder.category)
        * _held_partner_boost(bidder, opportunity)
    )
    return Valuation(
        value=total * multiplier,
        ceiling_rate=max_bid * bidder.live_multiplier.value,
        matches=matches,
    )


def _match(link: SegmentLink, rate: Decimal) -> SegmentMatch:
    return SegmentMatch(
        moment_segment=link.moment_segment,
        bid_segment=link.bid_segment,
        kind=link.kind,
        weight=link.weight,
        rate=rate * link.weight,
    )


def _invert(text: str) -> tuple[int, ...]:
    """Sort key that makes the lexically *smallest* id win a `>` comparison."""
    return tuple(-ord(char) for char in text) + (1,)


def price_ceiling(
    bidder: Bidder, valuation: Valuation, partners_present: frozenset[BrandId] | None = None
) -> Decimal:
    """The most this bidder can be charged per attention-second.

    That is its highest matched bid × live multiplier, plus the basket bonuses it
    offered for partners on screen (all of them when `partners_present` is None,
    for budget exposure).
    """
    bonuses = sum(
        (
            bonus.rate.amount
            for bonus in bidder.basket_bonuses
            if partners_present is None or bonus.partner in partners_present
        ),
        ZERO,
    )
    return valuation.ceiling_rate + bonuses


def _held_partner_boost(bidder: Bidder, opportunity: AdOpportunity) -> Decimal:
    confidence = max(
        (
            held.confidence.value
            for held in opportunity.held_products
            if held.brand_id != bidder.brand_id
            and held.brand_id in bidder.partners
            and opportunity.basket_approved(frozenset({held.brand_id, bidder.brand_id}))
        ),
        default=ZERO,
    )
    return ONE + HELD_PARTNER_BOOST * confidence / HUNDRED


def held_basket_boost(
    bidder: Bidder, basket: frozenset[BrandId], opportunity: AdOpportunity
) -> Decimal:
    """The held brand's own boost when an approved partner shares its basket.

    Someone holding a Café Patio cup is the moment to *complete* that basket. Only
    boosting the partner (above) would let the partner's solo ad crowd the held
    brand off the screen it's in the hand for; the basket that completes the held
    product earns the same lift on the held brand's slot. Ranking value only: the
    charge stays capped by the bid (`price_ceiling`).
    """
    confidence = max(
        (
            held.confidence.value
            for held in opportunity.held_products
            if held.brand_id == bidder.brand_id
            and any(
                partner in basket
                and opportunity.basket_approved(frozenset({held.brand_id, partner}))
                for partner in bidder.partners
            )
        ),
        default=ZERO,
    )
    return ONE + HELD_PARTNER_BOOST * confidence / HUNDRED
