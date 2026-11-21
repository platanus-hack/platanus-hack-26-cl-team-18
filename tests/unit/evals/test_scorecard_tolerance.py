"""The revenue-lift ratchet absorbs real-time run noise but still catches a real drop."""

import pytest
from evals.scorecard import failed, judge

BASELINE = {"basket_revenue_lift": 0.054349745474515765}


@pytest.mark.parametrize("value", [0.05323, 0.05157, 0.0544])
def test_run_to_run_noise_on_revenue_lift_is_not_a_regression(value: float) -> None:
    # 0.05157 and 0.05435 were both measured on the same sha (8d0026d).
    assert not failed(judge({"basket_revenue_lift": value}, BASELINE))


@pytest.mark.parametrize("value", [0.049, 0.02, 0.0, -0.1])
def test_a_real_revenue_lift_drop_still_fails_the_ratchet(value: float) -> None:
    (verdict,) = judge({"basket_revenue_lift": value}, BASELINE)
    assert verdict.status == "regressed"
