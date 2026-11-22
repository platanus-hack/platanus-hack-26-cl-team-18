"""Feed a stable duo, trio, held-product moment, or exit to a dedicated demo screen."""

from __future__ import annotations

import argparse
import time
from datetime import UTC, datetime

from scripts.replay_audience import snapshot_for
from websockets.sync.client import connect

from app.config.settings import AppSettings
from app.contract.messages import AudienceSnapshot, DetectedObject, DistanceBand, Tracklet

SCENES = {"duo": 2, "trio": 3, "held": 3, "empty": 0}
# The product a "held" scene shows, per catalog (its partner then completes the basket).
HELD_PRODUCT = {"fictional": "cafe-patio", "chile": "juan-valdez"}


def demo_snapshot(
    scene: str, screen: str, seq: int, dwell_s: float, *, held: str = "cafe-patio"
) -> AudienceSnapshot:
    party = SCENES[scene]
    snapshot = snapshot_for("co-brand" if party else "empty", seq, datetime.now(UTC))
    # One gym bottle and two coffee cups keep both complementary bids relevant.
    objects = (
        [DetectedObject(label=label, score=1) for label in ("bottle", "cup", "cup")]
        if party
        else []
    )
    if scene == "held":
        objects.append(DetectedObject(label=f"product:{held}", score=0.95))
    return snapshot.model_copy(
        update={
            "screen_id": screen,
            "people": party,
            "looking": party,
            "objects": objects,
            "sound_scene": [],
            "tracklets": [
                Tracklet(id=f"t{i}", dwell_s=dwell_s, looking=True, distance_band=DistanceBand.NEAR)
                for i in range(party)
            ],
        }
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scene", choices=SCENES)
    parser.add_argument("--screen", default="demo-01")
    parser.add_argument("--server", default="ws://127.0.0.1:8018")
    parser.add_argument("--seconds", type=int, choices=range(1, 121), default=12, metavar="1…120")
    args = parser.parse_args()
    held = HELD_PRODUCT[AppSettings().demo_catalog]
    start = time.monotonic()
    with connect(args.server.rstrip("/") + "/ws/agent") as agent:
        for seq in range(1, args.seconds * 5 + 1):
            agent.send(
                demo_snapshot(
                    args.scene, args.screen, seq, time.monotonic() - start, held=held
                ).model_dump_json()
            )
            time.sleep(max(0.0, start + seq / 5 - time.monotonic()))


if __name__ == "__main__":
    main()
