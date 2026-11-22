"""Every demo prop over the real loop: minor-safe always, and never worse than no prop.

Agent socket → classifier (catalog cues + the market's cue overrides) → auction →
creative → player, per catalog, for every label the perception agent can emit
(edge/agent/vision/vocabulary.py) and every held-product marker ("product:<slug>").

Two invariants:
- With `minor_likely`, no age-restricted brand ever wins (invariant 2).
- A watching group gets a paid ad with no prop at all (GroupDefault: a low-confidence
  coffee moment). Showing a prop must never turn that into the house ad.
"""

from __future__ import annotations

import ast
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cache
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.bootstrap.container import build_container
from app.bootstrap.demo import DEMO_SCREEN
from app.config.settings import AppSettings
from app.contract.messages import (
    CONSOLE_OUTBOUND,
    AudienceSnapshot,
    ConsoleAuction,
    CreativeReady,
    DetectedObject,
    DistanceBand,
    MinorLikely,
    Tracklet,
)
from app.main import create_app
from app.modules.common.infrastructure.clock.fixed_clock import FixedClock
from tests.unit.catalog_sweep.markets import CATALOGS, seeded

ROOT = Path(__file__).resolve().parents[3]
AT = datetime(2026, 11, 21, 15, tzinfo=UTC)  # midday at the venue (America/Santiago)
HOUSE = "ADverse"
# One scratch media dir for every run (bundle loops are not read here); removed at exit.
_MEDIA = tempfile.TemporaryDirectory(prefix="catalog-sweep-")


def _edge_labels() -> tuple[str, ...]:
    """Canonical wire labels of the agent's object vocabulary (read, never imported)."""
    source = (ROOT / "edge" / "agent" / "vision" / "vocabulary.py").read_text()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == (
            "OBJECT_VOCABULARY"
        ):
            pairs = ast.literal_eval(node.value) if node.value is not None else ()
            return tuple(sorted({label for _, label in pairs}))
    raise AssertionError("OBJECT_VOCABULARY not found in the edge vocabulary")


LABELS = _edge_labels()


@dataclass(frozen=True, slots=True)
class Aired:
    winners: tuple[str, ...]
    on_screen: tuple[str, ...]  # brand names in the creative's slots ("ADverse" = house ad)
    restricted: tuple[str, ...]  # winners that are age-restricted

    @property
    def paid(self) -> bool:
        return bool(self.winners) and HOUSE not in self.on_screen


@cache
def aired(catalog: str, label: str | None, minor: bool) -> Aired:
    settings = AppSettings(
        app_env="test",
        demo_simulator=True,
        media_dir=Path(_MEDIA.name),
        demo_catalog=catalog,
    )
    container = build_container(settings, clock=FixedClock(AT))
    snapshot = AudienceSnapshot(
        screen_id=DEMO_SCREEN,
        seq=1,
        measured_at=AT,
        people=2,
        looking=2,
        tracklets=[
            Tracklet(id=f"t{i}", dwell_s=1.0, looking=True, distance_band=DistanceBand.NEAR)
            for i in (1, 2)
        ],
        objects=[] if label is None else [DetectedObject(label=label, score=1)],
        minor_likely=MinorLikely(value=minor, confidence=1),
    )
    with (
        TestClient(create_app(container=container)) as client,
        client.websocket_connect(f"/ws/screen/{DEMO_SCREEN}") as player,
        client.websocket_connect("/ws/console") as console,
        client.websocket_connect("/ws/agent") as agent,
    ):
        player.receive_json()  # the house ad on connect
        agent.send_text(snapshot.model_dump_json())
        while not isinstance(
            auction := CONSOLE_OUTBOUND.validate_python(console.receive_json()), ConsoleAuction
        ):
            pass
        on_screen: tuple[str, ...] = (HOUSE,)
        if auction.winners:
            while (message := player.receive_json()).get("type") != "creative.ready":
                pass
            on_screen = tuple(
                s.brand_name for s in CreativeReady.model_validate(message).spec.slots
            )
    restricted = {b.name for b in seeded(catalog).catalog.brands if b.age_restricted}
    winners = tuple(w.brand_name for w in auction.winners)
    return Aired(
        winners=winners,
        on_screen=on_screen,
        restricted=tuple(w for w in winners if w in restricted),
    )


def _markers(catalog: str) -> list[str]:
    return [f"product:{brand.slug}" for brand in seeded(catalog).catalog.brands]


# Known gap: GroupDefault (app/modules/audience/domain/policies/group_default.py) only adds
# its coffee moment when the classifier found *no* segment, not when no segment is
# *biddable*, as its docstring promises. A prop whose segment nobody buys (Pets, Travel,
# Cycling...) replaces a paid coffee ad with the house ad. (Chile's food props, hot dog,
# pizza and sandwich, fill since 6133232 made the completo basket win: kept as cases.)
_GROUP_DEFAULT_GAP = (
    "GroupDefault applies only when no segment was found, so this prop's unbiddable or "
    "below-reserve segment turns the group's paid default moment into the house ad"
)
NO_FILL_GAPS = {
    "fictional": {
        "bicycle",
        "dog",
        "donut",
        "laptop",
        "pastry",
        "skateboard",
        "soda can",
        "suitcase",
    },
    "chile": {
        "beer bottle",
        "bicycle",
        "dog",
        "laptop",
        "skateboard",
        "suitcase",
        "wine glass",
    },
}
# Minor-likely groups: the age-restricted segment blocks its only bidders, and the
# group default still does not apply (same gap).
MINOR_NO_FILL_GAPS = {
    "fictional": NO_FILL_GAPS["fictional"] | {"beer bottle", "wine glass"},
    "chile": NO_FILL_GAPS["chile"],
}


def _fill_cases() -> list[object]:
    cases: list[object] = []
    for catalog in CATALOGS:
        for minor in (False, True):
            gaps = (MINOR_NO_FILL_GAPS if minor else NO_FILL_GAPS)[catalog]
            for label in (*LABELS, *_markers(catalog)):
                marks = (
                    [pytest.mark.xfail(strict=True, reason=_GROUP_DEFAULT_GAP)]
                    if label in gaps
                    else []
                )
                cases.append(
                    pytest.param(
                        catalog,
                        label,
                        minor,
                        marks=marks,
                        id=f"{catalog}-{label}-{'minor' if minor else 'adults'}",
                    )
                )
    return cases


@pytest.mark.parametrize("catalog", CATALOGS)
@pytest.mark.parametrize("minor", [False, True])
def test_a_watching_group_with_no_prop_gets_a_paid_ad(catalog: str, minor: bool) -> None:
    assert aired(catalog, None, minor).paid


@pytest.mark.parametrize(("catalog", "label", "minor"), _fill_cases())
def test_showing_a_prop_never_turns_a_paid_moment_into_the_house_ad(
    catalog: str, label: str, minor: bool
) -> None:
    result = aired(catalog, label, minor)
    assert result.paid, (result.winners, result.on_screen)
    assert result.on_screen == result.winners or set(result.on_screen) == set(result.winners)


@pytest.mark.parametrize("catalog", CATALOGS)
@pytest.mark.parametrize("label", [None, *LABELS])
def test_no_age_restricted_brand_wins_when_a_minor_is_likely(
    catalog: str, label: str | None
) -> None:
    labels = [label] if label is not None else [None]
    if label is None:
        labels += _markers(catalog)
    for each in labels:
        result = aired(catalog, each, True)
        assert not result.restricted, (each, result.restricted)
