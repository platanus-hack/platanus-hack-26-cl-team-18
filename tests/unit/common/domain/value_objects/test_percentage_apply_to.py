"""Regression: ``Percentage.apply_to`` must not double-round.

``apply_to`` used to multiply the amount by the *pre-rounded* ``to_ratio()`` — a
ratio already quantized to 4 dp (``SCALE = 0.0001``). For a fractional percentage
whose exact ratio carries more than 4 significant fractional digits, that pre-
rounding shifts the ratio before it is scaled up by a large amount, so the error is
amplified into a material monetary drift.

Concretely, ``2.575%`` of ``38_500_000``:
  * exact            -> 38_500_000 * 2.575 / 100        = 991_375.00
  * pre-rounded ratio-> to_ratio() = round(0.025750, 4) = 0.0258
                        38_500_000 * 0.0258             = 993_300.00   (drift +1_925)

The durable fix multiplies by the full-precision ``value / HUNDRED`` and rounds
exactly once, at the caller-supplied output scale. ``to_ratio()`` keeps its
quantized contract (it is a separate display/divisor helper, e.g. a gross-up
``1 + rate.to_ratio()``), so this change is isolated to ``apply_to``.

A whole-number rate such as 19% is exact at 4 dp (``19/100 = 0.19``), so its
output is unchanged — pinned below to prove the fix is a no-op for such rates.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from app.modules.common.domain.value_objects.percentage import Percentage


def test_apply_to_single_rounds_fractional_rate() -> None:
    """A fractional rate applied to a large base must round exactly once."""
    result = Percentage.create("2.575").apply_to(Decimal("38500000"))

    # Exact: 38_500_000 * 2.575 / 100. Double-rounding via to_ratio() gave 993_300.00.
    assert result == Decimal("991375.00")


def test_apply_to_whole_19_percent_is_unchanged() -> None:
    """19% is exact at 4 dp, so whole-number rates are unaffected by the fix."""
    assert Percentage.create("19").apply_to(Decimal("1000")) == Decimal("190.00")


def test_apply_to_respects_output_scale_and_rounding() -> None:
    """Output scale/rounding stay caller-controlled (e.g. CLP whole-peso)."""
    clp = Percentage.create("2.575").apply_to(
        Decimal("38500000"), scale=Decimal("1"), rounding=ROUND_HALF_UP
    )
    assert clp == Decimal("991375")


def test_apply_to_default_rounding_is_half_up_money_convention() -> None:
    """``apply_to`` defaults to ROUND_HALF_UP — the house money convention.

    Every caller applies a percentage to a *money* amount (revenue shares, discounts), so a
    banker's-rounding default (ROUND_HALF_EVEN) is a footgun: a caller that omits
    ``rounding`` silently diverges by a minor unit from the rest of the system at
    a ``.5`` quantum boundary. ``12.345`` sits exactly on the half-cent boundary:
    HALF_UP -> 12.35, HALF_EVEN -> 12.34.
    """
    assert Percentage.create("12.345").apply_to(Decimal("100")) == Decimal("12.35")


def test_to_ratio_keeps_quantized_contract() -> None:
    """``to_ratio`` is unchanged: still quantized to SCALE for divisor use."""
    assert Percentage.create("2.575").to_ratio() == Decimal("0.0258")
    assert Percentage.create("19").to_ratio() == Decimal("0.1900")
