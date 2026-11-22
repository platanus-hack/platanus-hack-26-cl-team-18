"""Crowded demo hall regressions (third vision audit): deterministic, no models, no camera.

Each test pins a failure measured by replaying adversarial augmentations of real
stock footage through the production pipeline (see edge/README.md, "Crowded hall").
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from agent.contract import AudienceSnapshot
from agent.vision.held import HELD_LABEL_PREFIX, HeldProductTracker, MarkerCatalog
from agent.vision.pipeline import VisionPipeline
from agent.vision.tracking import PersonTracker, TrackerConfig
from agent.vision.types import Box, FrameDetections, Keypoint, MarkerDetection, PersonDetection

T0 = datetime(2026, 11, 21, 15, tzinfo=UTC)
W, H = 1280, 720
CATALOG = MarkerCatalog(brands={0: "voltio", 1: "cafe-patio"})
FAST = TrackerConfig(min_hits=1)


def standing(
    x: float,
    *,
    top: float = 80,
    bottom: float = 700,
    width: float = 220,
    wrists: tuple[tuple[float, float], ...] | None = None,
) -> PersonDetection:
    """A pose detection; arms hang by the hips unless `wrists` says where the hands are."""
    cx = x + width / 2
    kps = [Keypoint(0, 0, 0.0)] * 17
    kps[0] = Keypoint(cx, top + 55, 0.9)
    kps[1] = Keypoint(cx + 18, top + 40, 0.9)
    kps[2] = Keypoint(cx - 18, top + 40, 0.9)
    shoulders_y = top + (bottom - top) * 0.2
    kps[5] = Keypoint(cx + width * 0.36, shoulders_y, 0.9)
    kps[6] = Keypoint(cx - width * 0.36, shoulders_y, 0.9)
    hips_y = top + (bottom - top) * 0.5
    arms = width * 0.43
    hands = wrists if wrists is not None else ((cx + arms, hips_y), (cx - arms, hips_y))
    for index, (wx, wy) in zip((9, 10), hands, strict=False):
        kps[index] = Keypoint(wx, wy, 0.8)
    return PersonDetection(box=Box(x, top, x + width, bottom), score=0.9, keypoints=tuple(kps))


def marker(marker_id: int, cx: float, cy: float, side: float = 30) -> MarkerDetection:
    half = side / 2
    return MarkerDetection(marker_id=marker_id, box=Box(cx - half, cy - half, cx + half, cy + half))


class Link:
    def __init__(self) -> None:
        self.snapshots: list[AudienceSnapshot] = []

    def send(self, message: object, *, binary: bytes | None = None) -> None:
        if isinstance(message, AudienceSnapshot):
            self.snapshots.append(message)


def held_labels(*people: PersonDetection, markers: tuple[MarkerDetection, ...]) -> set[str]:
    """Run 2 s of identical frames through the real pipeline; the held products it published."""
    link = Link()
    pipeline = VisionPipeline(
        screen_id="demo-01",
        source=None,  # type: ignore[arg-type]  # process() is driven directly
        detector=None,  # type: ignore[arg-type]
        link=link,
        tracker=PersonTracker(FAST),
        held=HeldProductTracker(CATALOG),
    )
    for i in range(20):
        at = T0 + timedelta(milliseconds=100 * i)
        pipeline.process(
            None, FrameDetections(at, W, H, persons=people, markers=markers, detector="replay")
        )
    return {
        o.label.removeprefix(HELD_LABEL_PREFIX)
        for s in link.snapshots
        for o in s.objects
        if o.label.startswith(HELD_LABEL_PREFIX)
    }


# ------------------------------------------------- held products: in a hand, not on a table


def test_a_prop_on_the_table_in_front_of_a_bystander_is_not_held() -> None:
    # The Café Patio cup waits on the demo table between beats. A visitor stands right
    # behind it with their arms down: the cup is inside their padded box, but not in a hand.
    bystander = standing(500)
    table_cup = marker(1, 610, 520)  # chest/table height, centred, 165 px from either wrist
    assert held_labels(bystander, markers=(table_cup,)) == set()


def test_a_prop_behind_a_passer_by_in_the_crowd_is_not_held() -> None:
    passer_by = standing(900, top=300, bottom=560, width=90)  # small: a few metres back
    prop_on_a_shelf = marker(0, 945, 360, side=14)
    assert held_labels(passer_by, markers=(prop_on_a_shelf,)) == set()


def test_a_prop_in_a_hand_is_held() -> None:
    judge = standing(500, wrists=((640, 470), (520, 400)))
    cup_in_hand = marker(1, 640, 440)  # the marker sits just above the wrist
    assert held_labels(judge, markers=(cup_in_hand,)) == {"cafe-patio"}


def test_a_prop_held_up_at_arms_length_is_still_held() -> None:
    # Extended arm toward the camera: the marker is big (closer) and a bit off the wrist.
    judge = standing(500, wrists=((700, 300), (520, 400)))
    cup = marker(1, 735, 255, side=60)
    assert held_labels(judge, markers=(cup,)) == {"cafe-patio"}


def test_two_props_in_two_hands_are_both_held() -> None:
    judge = standing(500, wrists=((700, 420), (520, 420)))
    both = (marker(1, 700, 395), marker(0, 520, 395))
    assert held_labels(judge, markers=both) == {"cafe-patio", "voltio"}


def test_a_detector_without_pose_keeps_the_box_rule() -> None:
    no_pose = PersonDetection(box=Box(500, 80, 720, 700), score=0.9)
    assert held_labels(no_pose, markers=(marker(1, 610, 520),)) == {"cafe-patio"}


# ------------------------------------------------- privacy: no face in a crowd reaches the VLM


def test_keyframe_hides_faces_the_hot_path_detector_missed() -> None:
    # In a crowd the hot-path pose pass (conf 0.35, 640 px) misses 25-75 % of the faces a
    # high-recall pass finds (measured on hall/market/queue stock footage). The allow-list
    # keyframe (docs/privacy.md G1) is black outside what the hot path found, so a missed
    # face in the background is hidden with no second pass. One standing *behind* a found
    # person sits inside a kept region; the high-recall pass blacks out its head (G9).
    np = pytest.importorskip("numpy")
    cv2 = pytest.importorskip("cv2", reason="needs `uv sync --extra vision`")
    from agent.vision.pipeline import redacted_encoder

    rng = np.random.default_rng(3)
    frame = rng.integers(40, 255, (H, W, 3), dtype=np.uint8)  # high-frequency "face" texture
    seen = standing(100, width=300)  # found by the hot path
    behind = standing(280, top=60, bottom=500, width=160)  # missed, inside seen's box
    far = standing(900, top=200, bottom=600, width=120)  # missed, in the background
    detections = FrameDetections(T0, W, H, persons=(seen,), detector="local")

    def decode(jpeg: bytes) -> object:
        return cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)

    plain = decode(redacted_encoder()(frame, detections).jpeg)
    both = decode(
        redacted_encoder(more_people=lambda _frame: [behind, far])(frame, detections).jpeg
    )

    def face(image: object, person: PersonDetection) -> object:
        s = 512 / W  # keyframes are downscaled to 512 px
        x, y = int(person.keypoints[0].x * s), int(person.keypoints[0].y * s)
        return image[y - 6 : y + 6, x - 6 : x + 6]  # type: ignore[index]

    def black(patch: object) -> bool:
        return float(patch.mean()) < 6  # type: ignore[attr-defined]

    assert black(face(plain, seen)) and black(face(both, seen))
    assert black(face(plain, far)) and black(face(both, far))  # allow-list alone hides it
    assert not black(face(plain, behind))  # inside a kept region: the hot path alone shows it
    assert black(face(both, behind))  # the high-recall heads close that gap


# ------------------------------------------------- people on other screens and rollup banners


def test_a_person_on_a_rollup_banner_in_an_ignored_region_is_not_the_audience() -> None:
    # Measured: a life-size frontal person on another booth's TV/banner added ~2 lookers
    # for as long as it was in view (hall: looking 2.2 -> 4.3). The venue masks that region.
    banner = standing(1000, top=40, bottom=330, width=150)
    visitor = standing(300)
    frame = FrameDetections(T0, W, H, persons=(banner, visitor))
    masked = TrackerConfig(min_hits=1, ignore_regions=((0.75, 0.0, 1.0, 0.5),))
    seen = PersonTracker(masked).update(frame)
    assert [t.box for t in seen] == [visitor.box]
    assert len(PersonTracker(FAST).update(frame)) == 2


def test_ignore_regions_parse_from_the_cli_and_reject_nonsense() -> None:
    from agent.vision.__main__ import argument_parser

    parser = argument_parser()
    args = parser.parse_args(["--screen", "s", "--ignore-region", "0.75,0,1,0.5"])
    assert args.ignore_region == [(0.75, 0.0, 1.0, 0.5)]
    assert parser.parse_args(["--screen", "s"]).ignore_region == []
    for bad in ("1,0,0.5,1", "0,0,1", "a,b,c,d"):
        with pytest.raises(SystemExit):
            parser.parse_args(["--screen", "s", "--ignore-region", bad])
