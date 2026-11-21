"""Project simulator decisions onto the operator console contract."""

from collections.abc import Mapping
from decimal import Decimal

from app.contract.messages import (
    AudienceSnapshot,
    BidLine,
    BidMatch,
    ConsoleAuction,
    ConsoleLatency,
    ConsoleMoment,
    CreativeOffer,
    Exclusion,
    ExclusionReason,
    HeldProductScore,
    LatencyTrace,
    MoneyDTO,
    SegmentMatchKind,
    SegmentScore,
    SignalSource,
    Winner,
)
from app.modules.auction.domain.result import AuctionResult, BidValue, Exclusion as DomainExclusion
from app.modules.audience import AudienceMomentSnapshot
from app.modules.audience.domain.value_objects.segment import SegmentId
from app.modules.campaigns.domain.book import Bidder, BrandId, BrandKit, CampaignId
from app.modules.common.domain.value_objects.money_rate import MoneyRate

_DETAIL = {
    "competitor": "same category as {other}",
    "not_partner": "not a pre-approved partner of {other}",
    "no_basket_offer": "no approved basket offer with {other}",
}


def _money(value: MoneyRate) -> MoneyDTO:
    return MoneyDTO(amount=format(value.amount.normalize(), "f"), currency=str(value.currency))


def decision_messages(
    *,
    snapshot: AudienceSnapshot,
    moment: AudienceMomentSnapshot,
    bidders: tuple[Bidder, ...],
    result: AuctionResult,
    kits: Mapping[BrandId, BrandKit],
    segment_labels: Mapping[SegmentId, str],
    auction_id: str,
    trace: LatencyTrace,
    creative_id: str | None,
    trigger: str,
    swap: str,
    offer: CreativeOffer | None = None,
) -> tuple[ConsoleMoment, ConsoleAuction] | tuple[ConsoleMoment, ConsoleAuction, ConsoleLatency]:
    screen_id = snapshot.screen_id
    by_campaign = {bidder.campaign_id: bidder for bidder in bidders}
    segments = {segment.segment_id: segment for segment in moment.segments}

    def name(campaign_id: CampaignId) -> str:
        return kits[by_campaign[campaign_id].brand_id].name

    def detail(exclusion: DomainExclusion) -> str:
        template = _DETAIL.get(exclusion.reason.value)
        if template is None or exclusion.related_campaign_id is None:
            return ""
        return f"{name(exclusion.campaign_id)}: " + template.format(
            other=name(exclusion.related_campaign_id)
        )

    def relevance(bid: BidValue) -> float:
        """Viewers × confidence the bid reaches, an approximate match by its weight."""
        return float(
            sum(
                segments[match.moment_segment].viewer_count
                * segments[match.moment_segment].confidence.value
                / 100
                * match.weight
                for match in bid.matches
            )
        )

    def best_rate(bid: BidValue) -> MoneyRate:
        """The most the bid pays per attention-second here: its rate × match weight."""
        currency = bid.value_per_s.currency
        return MoneyRate.create(
            currency=currency,
            amount=max((match.rate for match in bid.matches), default=Decimal(0)),
        )

    messages: tuple[ConsoleMoment, ConsoleAuction] = (
        ConsoleMoment(
            screen_id=screen_id,
            moment_id=str(moment.moment_id),
            people=moment.people,
            looking=moment.looking,
            segments=[
                SegmentScore(
                    segment_id=segment.segment_id.value,
                    label=segment_labels[segment.segment_id],
                    score=float(segment.confidence.value / 100),
                    sources=[SignalSource(source.value) for source in sorted(segment.sources)],
                )
                for segment in moment.segments
            ],
            minor_likely=snapshot.minor_likely,
            trace=trace,
            held_products=[
                HeldProductScore(
                    category=product.category,
                    brand_ref=product.brand_ref,
                    score=float(product.confidence.to_ratio()),
                )
                for product in moment.held_products
            ],
        ),
        ConsoleAuction(
            screen_id=screen_id,
            auction_id=auction_id,
            moment_id=str(moment.moment_id),
            bids=[
                BidLine(
                    campaign_id=str(bid.campaign_id),
                    brand_name=name(bid.campaign_id),
                    category=by_campaign[bid.campaign_id].category,
                    bid_per_as=_money(best_rate(bid)),
                    value=relevance(bid),
                    value_per_s=_money(bid.value_per_s),
                    multiplier=format(
                        by_campaign[bid.campaign_id].live_multiplier.value.normalize(), "f"
                    ),
                    matches=[
                        BidMatch(
                            segment_id=match.moment_segment.value,
                            bid_segment_id=match.bid_segment.value,
                            kind=SegmentMatchKind(match.kind.value),
                            weight=float(match.weight),
                            segment_label=segment_labels.get(
                                match.moment_segment, match.moment_segment.value
                            ),
                            bid_segment_label=segment_labels.get(
                                match.bid_segment, match.bid_segment.value
                            ),
                        )
                        for match in bid.matches
                    ],
                )
                for bid in result.bids
            ],
            exclusions=[
                Exclusion(
                    campaign_id=str(exclusion.campaign_id),
                    reason=ExclusionReason(exclusion.reason.value),
                    detail=detail(exclusion),
                )
                for exclusion in result.exclusions
            ],
            winners=[
                Winner(
                    campaign_id=str(winner.campaign_id),
                    brand_name=name(winner.campaign_id),
                    price_per_as=_money(winner.price_per_as),
                    payment_per_s=_money(winner.payment_per_s),
                )
                for winner in result.winners
            ],
            offer=offer,
            bundle_value=_money(result.bundle_value),
            single_brand_value=_money(result.single_brand_value),
            coverage=float(result.coverage),
            trace=trace,
            bundle_revenue=_money(result.bundle_revenue),
            single_brand_revenue=_money(result.single_brand_revenue),
            pricing=result.pricing.value,
            trigger=trigger,
            swap=swap,
        ),
    )
    if creative_id is None:
        return messages
    return (*messages, ConsoleLatency(screen_id=screen_id, creative_id=creative_id, trace=trace))
