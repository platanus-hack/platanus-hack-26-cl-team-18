"""Published campaign-book and brand-kit facts used by the simulator."""

from dataclasses import dataclass

from app.modules.audience.domain.value_objects.segment import SegmentId
from app.modules.common.domain.primitives.typed_id import TypedId
from app.modules.common.domain.value_objects.money import Money


class BrandId(TypedId):
    __slots__ = ()


class CampaignId(TypedId):
    __slots__ = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class CampaignBid:
    campaign_id: CampaignId
    brand_id: BrandId
    category: str
    age_restricted: bool
    segment_id: SegmentId
    bid_per_as: Money
    co_brand_with: frozenset[BrandId] = frozenset()


@dataclass(frozen=True, slots=True, kw_only=True)
class BrandKit:
    brand_id: BrandId
    name: str
    logo_url: str
    stock_copy: str
    accent_color: str
