"""What one bidder's offer is worth for this moment, in money per screen-second.

    v_c = min( Σ_s rate_c(s) · viewers(s) · confidence(s) · speech_boost(s),
               max_s rate_c(s) · speech_boost(s) · looking )
          · live_mult_c · context_mult(category_c) · held_partner_boost_c

The cap keeps overlapping segments from counting one viewer twice.

Boosts (speech intent, context, held product) are the platform's relevance
signals: they raise a bid's *value*, so it ranks higher, but never what the
brand can be *charged*. A segment bid is a contractual maximum price per
attention-second. Only the advertiser's own live multiplier and its opted-in
basket bonuses raise that ceiling (`price_ceiling`).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.modules.auction.domain.opportunity import ONE, ZERO, AdOpportunity
from app.modules.campaigns.domain.book import Bidder, BrandId

HUNDRED = Decimal(100)
# Someone *said* it: boost up to +50 % at full intent (R-C7). Capped by the
# per-bidder viewer cap above, so one loud person can't outweigh the group.
SPEECH_INTENT_ALPHA = Decimal("0.5")
# A held product boosts its approved partners up to +50 % (complete the basket, R-C6).
HELD_PARTNER_BOOST = Decimal("0.5")


@dataclass(frozen=True, slots=True, kw_only=True)
class Valuation:
    value: Decimal  # money per screen-second, boosts included (ranking)
    ceiling_rate: Decimal  # max chargeable per AS: highest matched bid × live multiplier
    matched: bool  # bids on at least one segment present in the moment


def valuate(bidder: Bidder, opportunity: AdOpportunity) -> Valuation:
    moment = opportunity.moment
    segments = {segment.segment_id: segment for segment in moment.segments}
    total = ZERO
    max_rate = ZERO
    max_bid = ZERO
    matched = False
    for bid in bidder.segment_bids:
        segment = segments.get(bid.segment_id)
        if segment is None:
            continue
        matched = True
        boost = ONE
        if segment.speech_intent is not None:
            boost += SPEECH_INTENT_ALPHA * segment.speech_intent.value / HUNDRED
        rate = bid.rate.amount * boost
        total += rate * segment.viewer_count * segment.confidence.value / HUNDRED
        max_rate = max(max_rate, rate)
        max_bid = max(max_bid, bid.rate.amount)
    total = min(total, max_rate * moment.looking)
    multiplier = (
        bidder.live_multiplier.value
        * opportunity.multiplier_for(bidder.category)
        * _held_partner_boost(bidder, opportunity)
    )
    return Valuation(
        value=total * multiplier,
        ceiling_rate=max_bid * bidder.live_multiplier.value,
        matched=matched,
    )


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
