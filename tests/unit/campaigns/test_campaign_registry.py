"""Campaign identities, budgets and live bids outlive the process (CampaignRegistry)."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

import pytest

from app.bootstrap.container import build_container
from app.bootstrap.demo import seed_market
from app.config.settings import AppSettings
from app.modules.campaigns.application.live_book import LiveCampaignBook, MultiplierOutcome
from app.modules.campaigns.application.ports.registry import StoredCampaign
from app.modules.campaigns.domain.book import BidMultiplier, CampaignId
from app.modules.campaigns.infrastructure.registry import InMemoryCampaignRegistry
from app.modules.common.application.id_generator import IdGenerator
from app.modules.common.domain.value_objects.money import Money
from tests.unit.auction.builders import FITNESS, bidder


def clp(amount: int) -> Money:
    return Money.create(currency="CLP", amount=amount)


class RestoringRegistry(InMemoryCampaignRegistry):
    """What a Postgres registry returns after a restart: prior spend and bids."""

    def __init__(self, *, spent: int, multiplier: str, seq: int) -> None:
        super().__init__()
        self._spent, self._multiplier, self._seq = clp(spent), multiplier, seq
        self.recorded: list[tuple[CampaignId, BidMultiplier, int]] = []

    def open(self, slugs: Sequence[str], *, budget: Money) -> dict[str, StoredCampaign]:
        fresh = super().open(slugs, budget=budget)
        return {
            slug: StoredCampaign(
                slug=slug,
                brand_id=c.brand_id,
                campaign_id=c.campaign_id,
                budget=c.budget,
                spent=self._spent,
                multiplier=BidMultiplier.create(self._multiplier),
                multiplier_seq=self._seq,
            )
            for slug, c in fresh.items()
        }

    def multiplier_changed(
        self, campaign_id: CampaignId, multiplier: BidMultiplier, seq: int
    ) -> None:
        self.recorded.append((campaign_id, multiplier, seq))


def test_the_book_restores_spend_and_seq_and_reports_only_applied_multipliers() -> None:
    seen: list[int] = []
    one = bidder(1, "fitness", {FITNESS: 10}, budget=1000)
    book = LiveCampaignBook(
        [one],
        spent={one.campaign_id: clp(400)},
        multiplier_seqs={one.campaign_id: 10},
        on_multiplier=lambda _campaign, _multiplier, seq: seen.append(seq),
    )
    (entry,) = book.entries()
    assert entry.account is not None and entry.account.spent == clp(400)
    assert book.snapshot()[0].budget_remaining == clp(600)
    up = BidMultiplier.create("1.5")
    assert book.set_live_multiplier(one.campaign_id, up, 9) is MultiplierOutcome.STALE
    assert book.set_live_multiplier(one.campaign_id, up, 11) is MultiplierOutcome.APPLIED
    assert book.set_live_multiplier(one.campaign_id, up, 11) is MultiplierOutcome.DUPLICATE
    assert seen == [11]


def test_one_registry_gives_the_same_campaign_ids_on_every_seed() -> None:
    registry = InMemoryCampaignRegistry()
    first = seed_market(ids=IdGenerator(), budget_clp=50_000, registry=registry)
    again = seed_market(ids=IdGenerator(), budget_clp=50_000, registry=registry)
    assert [b.campaign_id for b in first.bidders] == [b.campaign_id for b in again.bidders]
    assert [k.brand_id for k in first.kits] == [k.brand_id for k in again.kits]


def test_a_restored_market_keeps_spend_and_the_last_bid() -> None:
    registry = RestoringRegistry(spent=45_000, multiplier="2.5", seq=1_700_000)
    market = seed_market(ids=IdGenerator(), budget_clp=50_000, registry=registry)
    first = market.book.snapshot()[0]
    assert first.budget_remaining == clp(5_000)
    assert first.live_multiplier == BidMultiplier.create("2.5")
    entry = market.book.entries()[0]
    assert entry.multiplier_seq == 1_700_000
    outcome = market.book.set_live_multiplier(
        first.campaign_id, BidMultiplier.create(Decimal("0.5")), 1_700_001
    )
    assert outcome is MultiplierOutcome.APPLIED
    assert registry.recorded == [(first.campaign_id, BidMultiplier.create("0.5"), 1_700_001)]


class BrokenRegistry(InMemoryCampaignRegistry):
    def open(self, slugs: Sequence[str], *, budget: Money) -> dict[str, StoredCampaign]:
        raise ConnectionError("database down")


def test_a_database_down_at_boot_still_starts_the_market(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from app.bootstrap import container as container_module

    def records_with_broken_registry(container: container_module.Container) -> None:
        container.campaign_registry = BrokenRegistry()

    monkeypatch.setattr(container_module, "build_records", records_with_broken_registry)
    built = build_container(AppSettings(_env_file=None, app_env="test"))
    assert built.demo is not None
    assert "Campaign registry unavailable" in caplog.text
