"""The player's distance-aware layout follows the nearest looking viewer."""

import pytest

from app.bootstrap.demo import nearest_band
from app.contract.messages import DistanceBand, Tracklet


def _t(band: DistanceBand, *, looking: bool = True, n: int = 0) -> Tracklet:
    return Tracklet(id=f"t{n}", dwell_s=1.0, looking=looking, distance_band=band)


@pytest.mark.parametrize(
    ("tracklets", "expected"),
    [
        ([], None),
        ([_t(DistanceBand.FAR)], DistanceBand.FAR),
        ([_t(DistanceBand.FAR), _t(DistanceBand.MID, n=1)], DistanceBand.MID),
        ([_t(DistanceBand.FAR), _t(DistanceBand.NEAR, n=1)], DistanceBand.NEAR),
        # Someone near but looking away doesn't make the screen show the QR up front.
        ([_t(DistanceBand.NEAR, looking=False), _t(DistanceBand.FAR, n=1)], DistanceBand.FAR),
        ([_t(DistanceBand.NEAR, looking=False)], None),
    ],
)
def test_nearest_looking_band(tracklets: list[Tracklet], expected: DistanceBand | None) -> None:
    assert nearest_band(tracklets) == expected
