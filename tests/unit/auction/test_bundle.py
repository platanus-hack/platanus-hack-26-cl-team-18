from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from scripts.replay_audience import snapshot_for

from app.modules.auction.domain.bundle import AdOpportunity, Layout, clear_bundle
from app.modules.audience import AudienceMomentSnapshot
from app.modules.audience.application.fixture_tagging import classify_snapshot
from app.modules.audience.domain.value_objects.ids import MomentId
from app.modules.audience.domain.value_objects.segment import SegmentId
from app.modules.campaigns.domain.book import BrandId, CampaignBid, CampaignId
from app.modules.common.domain.value_objects.money import Money
from app.modules.common.domain.value_objects.utc_timestamp import UtcTimestamp
from app.modules.screens.domain.value_objects.ids import ScreenId

T0 = datetime(2026, 11, 21, tzinfo=UTC)


def opportunity_for_test() -> AdOpportunity:
    fitness = SegmentId.create("DEMO_FITNESS")
    coffee = SegmentId.create("DEMO_COFFEE")
    left = BrandId.create(UUID("018f0000-0000-7000-8000-000000000001"))
    right = BrandId.create(UUID("018f0000-0000-7000-8000-000000000002"))
    instant = UtcTimestamp.create(T0)
    return AdOpportunity(
        moment=AudienceMomentSnapshot.create(
            screen_id=ScreenId.create(UUID("018f0000-0000-7000-8000-000000000003")),
            moment_id=MomentId.create(UUID("018f0000-0000-7000-8000-000000000004")),
            measured_at=instant,
            received_at=instant,
            moment_changed_at=instant,
            people=2,
            looking=2,
            segments=classify_snapshot(
                snapshot_for("co-brand", 1, T0),
                vision_catalog={"sports ball": fitness},
                sound_catalog={"coffee service fixture": coffee},
            ),
            minor_likely=False,
        ),
        campaigns=(
            CampaignBid(
                campaign_id=CampaignId.create(UUID("018f0000-0000-7000-8000-000000000005")),
                brand_id=left,
                category="sports_goods",
                age_restricted=False,
                segment_id=fitness,
                bid_per_as=Money.create(currency="CLP", amount=100),
                co_brand_with=frozenset({right}),
            ),
            CampaignBid(
                campaign_id=CampaignId.create(UUID("018f0000-0000-7000-8000-000000000006")),
                brand_id=right,
                category="coffee",
                age_restricted=False,
                segment_id=coffee,
                bid_per_as=Money.create(currency="CLP", amount=100),
                co_brand_with=frozenset({left}),
            ),
        ),
        layouts=(
            Layout(name="solo", shares=(Decimal(1),)),
            Layout(name="duo", shares=(Decimal("0.5"), Decimal("0.5"))),
        ),
        reserve=Money.create(currency="CLP", amount=10),
        pair_affinity=Money.create(currency="CLP", amount=20),
    )


def test_clears_compatible_bundle_and_prices_externality() -> None:
    opportunity = opportunity_for_test()
    result = clear_bundle(opportunity)
    assert result.layout is not None and result.layout.name == "duo"
    assert result.bundle_value.amount == 120
    assert result.single_brand_value.amount == 100
    assert [winner.price_per_as.amount for winner in result.winners] == [30, 30]
    assert clear_bundle(replace(opportunity, campaigns=opportunity.campaigns[::-1])) == result


def test_excludes_competitors_even_when_both_opt_in() -> None:
    opportunity = opportunity_for_test()
    first, second = opportunity.campaigns
    competing = replace(second, category=first.category)
    result = clear_bundle(replace(opportunity, campaigns=(competing, first)))
    assert result.layout is not None and result.layout.name == "solo"
    assert [winner.bid for winner in result.winners] == [first]


def test_returns_no_fill_when_reserve_is_above_every_bundle() -> None:
    opportunity = opportunity_for_test()
    result = clear_bundle(replace(opportunity, reserve=Money.create(currency="CLP", amount=200)))
    assert result.layout is None and not result.winners
    assert result.single_brand_value.amount == 100
