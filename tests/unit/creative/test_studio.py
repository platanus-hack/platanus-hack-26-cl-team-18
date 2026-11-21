"""Studio: hot compose, warm guarded upgrades, cost and budget (no network)."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import anyio
import pytest

from app.modules.common.infrastructure.clock.fixed_clock import FixedClock
from app.modules.creative.application.ports.copywriter import (
    CopyFailure,
    CopyWriterError,
    TokenUsage,
    WrittenCopy,
)
from app.modules.creative.application.studio import (
    CreativeStudio,
    Outcome,
    StudioConfig,
    ai_wins,
)
from app.modules.creative.domain.angle import Angle
from app.modules.creative.domain.brief import CreativeBrief
from app.modules.creative.domain.cost import (
    CostModel,
    DenialReason,
    GenerationBudget,
    GenerationKind,
    GenerationLedger,
    UsdCost,
    plan_upgrades,
)
from app.modules.creative.domain.draft import CopyDraft, CopySource
from app.modules.creative.domain.scoring import score_draft
from tests.unit.creative.factory import brief

NOW = datetime(2026, 11, 21, 12, tzinfo=UTC)
GROUP = brief(party=4, angle=Angle.HUNGER)
BETTER = CopyDraft(
    teaser="¿Bajón los 4? Voltio + Café Patio",
    offer="Para los 4 · Pack x4 post-entreno: Voltio + Café Patio por $10.970",
    close="Muestra el QR en caja · válido 10 min",
    voice_line="Para los 4: Voltio y Café Patio por $10.970",
    source=CopySource.AI,
    model_version="fake@low",
)


class FakeWriter:
    name = "fake"

    def __init__(
        self,
        draft: CopyDraft = BETTER,
        *,
        fail: CopyFailure | None = None,
        delay: float = 0.0,
    ) -> None:
        self.draft, self.fail, self.delay, self.calls = draft, fail, delay, 0

    async def write(self, b: CreativeBrief) -> WrittenCopy:
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail is not None:
            raise CopyWriterError(self.fail)
        return WrittenCopy(draft=self.draft, usage=TokenUsage(input_tokens=900, output_tokens=200))


def studio(writer: FakeWriter | None = None, **kwargs: object) -> CreativeStudio:
    return CreativeStudio(clock=FixedClock(NOW), writer=writer, **kwargs)  # type: ignore[arg-type]


def run(coro):  # type: ignore[no-untyped-def]
    return anyio.run(lambda: coro)


def test_compose_is_targeted_stock_without_a_writer() -> None:
    composed = studio().compose(GROUP)
    assert composed.draft.source is CopySource.STOCK
    assert "los 4" in composed.draft.teaser and "hambre" in composed.draft.teaser
    assert "$10.970" in composed.draft.offer
    assert composed.score.verdict.passed  # latency is gated by the eval, not a unit test


def test_accepted_ai_copy_is_cached_and_served_hot() -> None:
    s = studio(FakeWriter())
    assert ai_wins(score_draft(BETTER, GROUP), score_draft(s.stock(GROUP), GROUP), Decimal("0.05"))
    upgraded = run(s.upgrade_copy(GROUP, value_per_s_clp=Decimal(20)))
    assert upgraded == BETTER
    composed = s.compose(GROUP)
    assert composed.draft.source is CopySource.CACHE and composed.draft.teaser == BETTER.teaser
    assert s.stats.outcomes == {Outcome.ACCEPTED: 1}
    assert s.ledger.total() == CostModel().copy(input_tokens=900, output_tokens=200)
    assert not s.needs_upgrade(GROUP)


@pytest.mark.parametrize(
    ("writer", "outcome"),
    [
        (FakeWriter(fail=CopyFailure.UNAVAILABLE), Outcome.FAILED),
        (FakeWriter(fail=CopyFailure.INVALID_OUTPUT), Outcome.FAILED),
        (FakeWriter(fail=CopyFailure.QUOTA), Outcome.FAILED),
    ],
)
def test_provider_failures_keep_stock(writer: FakeWriter, outcome: Outcome) -> None:
    s = studio(writer)
    assert run(s.upgrade_copy(GROUP, value_per_s_clp=Decimal(20))) is None
    assert s.stats.outcomes == {outcome: 1}
    assert s.compose(GROUP).draft.source is CopySource.STOCK


def test_quota_disables_the_writer_for_the_session() -> None:
    writer = FakeWriter(fail=CopyFailure.QUOTA)
    s = studio(writer)
    run(s.upgrade_copy(GROUP, value_per_s_clp=Decimal(20)))
    run(s.upgrade_copy(brief(party=2, angle=Angle.HUNGER), value_per_s_clp=Decimal(20)))
    assert writer.calls == 1 and not s.ai_enabled
    assert s.stats.outcomes[Outcome.DISABLED] == 1


def test_guard_rejection_keeps_stock_and_records_why() -> None:
    bad = replace(BETTER, offer="Para los 4 · Voltio + Café Patio por $8.990")
    s = studio(FakeWriter(bad))
    assert run(s.upgrade_copy(GROUP, value_per_s_clp=Decimal(20))) is None
    (record,) = s.stats.records
    assert record.outcome is Outcome.REJECTED and record.ai_score == 0
    assert s.compose(GROUP).draft.source is CopySource.STOCK


def test_safe_but_worse_copy_does_not_replace_stock() -> None:
    bland = CopyDraft(
        teaser="Voltio + Café Patio",
        offer="Voltio + Café Patio por $10.970",
        close="QR",
        voice_line="Voltio y Café Patio",
        source=CopySource.AI,
    )
    s = studio(FakeWriter(bland))
    assert run(s.upgrade_copy(GROUP, value_per_s_clp=Decimal(20))) is None
    assert s.stats.records[-1].outcome is Outcome.LOWER_SCORE


def test_timeout_books_the_estimate_and_keeps_stock() -> None:
    s = studio(FakeWriter(delay=0.2), config=StudioConfig(copy_timeout_s=0.01))
    assert run(s.upgrade_copy(GROUP, value_per_s_clp=Decimal(20))) is None
    assert s.stats.records[-1].outcome is Outcome.TIMEOUT
    assert s.ledger.total() == s.copy_estimate()


def test_hourly_cap_denies_upgrades() -> None:
    ledger = GenerationLedger()
    ledger.record(GenerationKind.VIDEO, UsdCost.create("1.999999"), at=NOW)
    s = studio(FakeWriter(), ledger=ledger, budget=GenerationBudget(hourly_cap=UsdCost.create(2)))
    assert run(s.upgrade_copy(GROUP, value_per_s_clp=Decimal(20))) is None
    assert s.stats.records[-1].outcome is Outcome.BUDGET_DENIED


def test_prewarm_caches_each_brief_once() -> None:
    writer = FakeWriter()
    s = studio(writer)
    cached = run(s.prewarm([GROUP, GROUP]))
    assert cached == 1 and writer.calls == 1 and GROUP.signature() in s.cache


def test_roi_rule_spends_on_copy_but_not_on_video_for_a_small_moment() -> None:
    budget = GenerationBudget()
    costs = CostModel()
    decisions = plan_upgrades(
        [
            (GenerationKind.COPY, costs.copy(input_tokens=900, output_tokens=220)),
            (GenerationKind.VOICE, costs.voice(60)),
            (GenerationKind.VIDEO, costs.video(5)),
        ],
        expected_revenue=Decimal("0.10"),  # ~CLP 95 over the dwell
        ledger=GenerationLedger(),
        budget=budget,
        at=NOW,
    )
    assert [d.approved for d in decisions] == [True, True, False]
    assert decisions[2].reason is DenialReason.NOT_WORTH_IT


def test_usd_cost_is_exact_micros() -> None:
    copy = CostModel().copy(input_tokens=900, output_tokens=220)
    assert copy == UsdCost(micros=200)  # 900 * 0.10/1e6 + 220 * 0.50/1e6 = $0.0002
    assert str(UsdCost.create("0.0002")) == "$0.000200"
    with pytest.raises(TypeError):
        UsdCost.create(0.1)  # type: ignore[arg-type]


def test_a_crowd_renders_with_the_largest_offer_and_no_count() -> None:
    """Regression: 15 people looking crashed render_spec (CreativeOffer.party_size <= 12)."""
    from app.modules.campaigns.domain.book import BrandKit
    from app.modules.creative.application.rendering import render_spec
    from tests.unit.creative.factory import BRANDS

    kits = {
        b: BrandKit(
            brand_id=b,
            name=v.name,
            logo_url="/l.png",
            stock_copy=v.stock_copy,
            accent_color="#ffffff",
        )
        for b, v in BRANDS.items()
    }
    crowd = brief(party=15, angle=Angle.HUNGER)
    composed = studio().compose(crowd)
    spec = render_spec(crowd, composed.draft, kits)
    assert spec.offer is not None and spec.offer.party_size == 4
    assert "todo el grupo" in spec.stages[1].copy_text and composed.score.verdict.passed
