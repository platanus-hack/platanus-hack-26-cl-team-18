"""Project simulator decisions onto the operator console contract."""

from collections.abc import Mapping

from app.contract.messages import (
    AudienceSnapshot,
    BidLine,
    ConsoleAuction,
    ConsoleLatency,
    ConsoleMoment,
    CreativeReady,
    Exclusion,
    ExclusionReason,
    MoneyDTO,
    SegmentScore,
    SignalSource,
    Winner,
)
from app.modules.auction.domain.bundle import AuctionResult
from app.modules.audience import AudienceMomentSnapshot
from app.modules.audience.domain.value_objects.segment import SegmentId
from app.modules.campaigns.domain.book import BrandId, BrandKit, CampaignBid
from app.modules.common.domain.value_objects.money import Money


def _money(value: Money) -> MoneyDTO:
    return MoneyDTO(amount=format(value.amount, "f"), currency=str(value.currency))


def decision_messages(
    *,
    snapshot: AudienceSnapshot,
    moment: AudienceMomentSnapshot,
    campaigns: tuple[CampaignBid, ...],
    result: AuctionResult,
    kits: Mapping[BrandId, BrandKit],
    segment_labels: Mapping[SegmentId, str],
    auction_id: str,
    creative: CreativeReady,
) -> tuple[ConsoleMoment, ConsoleAuction, ConsoleLatency]:
    screen_id = creative.screen_id
    trace = creative.trace
    segments = {segment.segment_id: segment for segment in moment.segments}
    matching_campaigns = tuple(bid for bid in campaigns if bid.segment_id in segments)
    covered = {winner.bid.segment_id for winner in result.winners}
    return (
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
        ),
        ConsoleAuction(
            screen_id=screen_id,
            auction_id=auction_id,
            moment_id=str(moment.moment_id),
            bids=[
                BidLine(
                    campaign_id=str(bid.campaign_id),
                    brand_name=kits[bid.brand_id].name,
                    category=bid.category,
                    bid_per_as=_money(bid.bid_per_as),
                    value=float(
                        segments[bid.segment_id].viewer_count
                        * segments[bid.segment_id].confidence.value
                        / 100
                    ),
                )
                for bid in matching_campaigns
            ],
            exclusions=[
                Exclusion(
                    campaign_id=str(exclusion.campaign_id),
                    reason=ExclusionReason(exclusion.reason.value),
                )
                for exclusion in result.exclusions
            ],
            winners=[
                Winner(
                    campaign_id=str(winner.bid.campaign_id),
                    brand_name=kits[winner.bid.brand_id].name,
                    price_per_as=_money(winner.price_per_as),
                )
                for winner in result.winners
            ],
            bundle_value=_money(result.bundle_value),
            single_brand_value=_money(result.single_brand_value),
            coverage=len(covered) / len(segments) if segments else 0,
            trace=trace,
        ),
        ConsoleLatency(screen_id=screen_id, creative_id=creative.creative_id, trace=trace),
    )
