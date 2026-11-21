"""Hook testing: a deterministic bandit over approved hooks, fed by attention and scans."""

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.modules.common.infrastructure.clock.fixed_clock import FixedClock
from app.modules.creative.application.hook_lab import HookLab
from app.modules.creative.application.stock_copy import StockCopywriter, hook_options
from app.modules.creative.application.studio import CreativeStudio
from app.modules.creative.domain.angle import Angle
from app.modules.creative.domain.draft import CopySource
from app.modules.creative.domain.guard import check_copy
from app.modules.creative.domain.hook_test import SCAN_WORTH_S, ArmStats, pick_arm
from tests.unit.creative.factory import PATIO, VOLTIO, brief

NOW = datetime(2026, 11, 21, 15, tzinfo=UTC)


def test_untried_hooks_air_first_in_order() -> None:
    assert pick_arm([ArmStats(), ArmStats()]) == 0
    assert pick_arm([ArmStats(plays=1, attention_s=Decimal(9)), ArmStats()]) == 1


def test_the_hook_that_holds_attention_wins_once_both_are_known() -> None:
    weak = ArmStats(plays=20, attention_s=Decimal(40))  # 2 s per play
    strong = ArmStats(plays=20, attention_s=Decimal(160))  # 8 s per play
    assert pick_arm([weak, strong]) == 1
    assert pick_arm([strong, weak]) == 0


def test_a_scan_is_worth_ten_seconds_of_attention() -> None:
    scanned = ArmStats(plays=1, attention_s=Decimal(2)).scanned()
    assert scanned.mean_reward() == Decimal(2) + SCAN_WORTH_S


def test_ties_go_to_the_targeted_hook_and_the_pick_is_reproducible() -> None:
    same = ArmStats(plays=5, attention_s=Decimal(20))
    assert pick_arm([same, same, same]) == 0
    arms = [ArmStats(plays=3, attention_s=Decimal(9)), ArmStats(plays=4, attention_s=Decimal(14))]
    assert {pick_arm(arms) for _ in range(5)} == {pick_arm(arms)}


def test_exploration_revisits_a_hook_with_few_plays() -> None:
    proven = ArmStats(plays=200, attention_s=Decimal(1000))  # 5 s per play
    barely_tried = ArmStats(plays=1, attention_s=Decimal(3))  # 3 s, one play
    assert pick_arm([proven, barely_tried]) == 1


@pytest.mark.parametrize("bad", [{"plays": -1}, {"scans": -1}, {"attention_s": Decimal(-1)}])
def test_stats_cannot_go_negative(bad: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        ArmStats(**bad)  # type: ignore[arg-type]


def test_the_lab_credits_plays_and_scans_to_the_hook_that_aired() -> None:
    lab = HookLab()
    assert lab.variant_for("brief-a", 1) == 0  # nothing to test
    first = lab.variant_for("brief-a", 2)
    lab.aired("c1", "brief-a", first, "Hook A")
    lab.observe_play("c1", Decimal("6.5"))
    lab.observe_scan("c1")
    second = lab.variant_for("brief-a", 2)
    lab.aired("c2", "brief-a", second, "Hook B")
    lab.observe_play("c2", Decimal(1))
    lab.observe_play("unknown", Decimal(99))  # a house ad or forgotten creative: ignored
    assert (first, second) == (0, 1)
    rows = {r.variant: r for r in lab.results()}
    assert rows[0].hook == "Hook A" and rows[0].stats == ArmStats(
        plays=1, attention_s=Decimal("6.5"), scans=1
    )
    assert rows[1].stats.plays == 1 and rows[1].stats.attention_s == Decimal(1)
    assert [r.variant for r in lab.results()] == [0, 1]  # best mean first


@pytest.mark.parametrize("angle", [a for a in Angle if a is not Angle.SOCIAL])
@pytest.mark.parametrize("party", [1, 3, 9])
def test_every_hook_option_is_approved_copy(angle: Angle, party: int) -> None:
    held = "Café Patio" if angle is Angle.COMPLETE_BASKET else None
    b = brief(VOLTIO, PATIO, party=party, angle=angle, held=held)
    lead, *rest = b.brands
    b = replace(b, brands=(replace(lead, tagline="Voltio. Una vuelta más."), *rest))
    options = hook_options(b)
    assert len(options) >= 2 and len(set(options)) == len(options)
    for variant in range(len(options)):
        draft = StockCopywriter().compose(b, variant=variant)
        assert draft.teaser == options[variant]
        assert check_copy(draft, b).passed, (draft.teaser, check_copy(draft, b).violations)


def test_cached_ai_copy_is_not_split_into_a_test() -> None:
    b = brief(VOLTIO, PATIO, party=2, angle=Angle.POST_WORKOUT)
    studio = CreativeStudio(clock=FixedClock(NOW))
    assert studio.hook_count(b) == len(hook_options(b)) > 1
    studio.cache.put(
        b.signature(),
        replace(
            StockCopywriter().compose(b),
            source=CopySource.AI,
        ),
    )
    assert studio.hook_count(b) == 1


def test_the_studio_airs_the_requested_hook() -> None:
    b = brief(VOLTIO, PATIO, party=2, angle=Angle.POST_WORKOUT)
    studio = CreativeStudio(clock=FixedClock(NOW))
    assert studio.compose(b, variant=1).draft.teaser == hook_options(b)[1]
    assert studio.compose(b).draft.teaser == hook_options(b)[0]
