"""Speech in a noisy hall, over the real demo loop (third speech audit).

A crowd says food words all the time ("café" from the next table, a PA, our own
voice-over leaking through the speakerphone). Whatever reaches the backend as
utterances, the screen must not strobe, spoken topics must expire once the mic
goes quiet, and speech must never put an age-restricted brand in front of a minor.

In-process: fixed clock, manual scheduler, the hub's screen sends recorded.
"""

from __future__ import annotations

import random
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from scripts.replay_audience import snapshot_for

from app.bootstrap.container import build_container
from app.bootstrap.demo_catalog_chile import CHILE
from app.config.settings import AppSettings
from app.contract.messages import AudienceUtterance, CreativeReady, MinorLikely
from app.main import create_app
from app.modules.common.infrastructure.clock.fixed_clock import FixedClock
from app.realtime.router import Received

from .test_auction_loop_e2e import ManualScheduler

T0 = datetime(2026, 11, 21, 15, tzinfo=UTC)
TICK = timedelta(milliseconds=500)
CROWD_WORDS = ("café", "hambre", "agua", "cansado", "completo", "gimnasio", "chela", "tomemos once")
AGE_RESTRICTED = frozenset(brand.name for brand in CHILE.brands if brand.age_restricted)


@dataclass
class Hall:
    clock: FixedClock
    scheduler: ManualScheduler
    container: object
    aired: list[CreativeReady] = field(default_factory=list)
    seq: int = 0
    said: int = 0

    def tick(self, *, people: int, minor: bool = False, word: str | None = None) -> None:
        self.clock.advance(TICK)
        self.scheduler.fire_due()
        self.seq += 1
        snapshot = snapshot_for("fitness", self.seq, self.clock.now_utc()).model_copy(
            update={
                "people": people,
                "looking": people,
                "minor_likely": MinorLikely(value=minor, confidence=1),
            }
        )
        router = self.container.router  # type: ignore[attr-defined]
        router.dispatch_sync(snapshot, Received(received_at=self.clock.now_utc()))
        if word is not None:
            self.said += 1
            router.dispatch_sync(
                AudienceUtterance(
                    screen_id="demo-01",
                    seq=self.said,
                    measured_at=self.clock.now_utc(),
                    text=word,
                    language="es",
                    keyterms_hit=[],
                    is_final=self.said % 3 == 0,
                    utterance_id=f"crowd:{self.said}",
                ),
                Received(received_at=self.clock.now_utc()),
            )

    def baskets(self) -> list[tuple[str, ...]]:
        return [
            tuple(sorted(slot.brand_name for slot in creative.spec.slots))
            for creative in self.aired
            if creative.auction_id is not None
        ]

    def swaps(self) -> int:
        baskets = self.baskets()
        return sum(1 for a, b in pairwise(baskets) if a != b)


@pytest.fixture
def hall(tmp_path: Path) -> Iterator[Hall]:
    clock = FixedClock(T0)
    scheduler = ManualScheduler(clock)
    settings = AppSettings(
        app_env="test", demo_simulator=True, media_dir=tmp_path, speech_mode_screens=["demo-01"]
    )
    container = build_container(settings, clock=clock, scheduler=scheduler)
    rig = Hall(clock, scheduler, container)
    with TestClient(create_app(container=container)):

        def record(message: object, *, sink: object = None) -> None:
            if isinstance(message, CreativeReady):
                rig.aired.append(message)

        container.hub.send_to_screen_threadsafe = record  # type: ignore[method-assign]
        yield rig


def crowd(seconds: float, per_minute: float, seed: int = 1) -> Iterator[str | None]:
    rng = random.Random(seed)
    chance = per_minute / 60 * TICK.total_seconds()
    for _ in range(int(seconds / TICK.total_seconds())):
        yield rng.choice(CROWD_WORDS) if rng.random() < chance else None


def test_a_group_screen_does_not_strobe_under_constant_crowd_food_words(hall: Hall) -> None:
    # 30 food words a minute from the room, for a minute: measured 2 swaps/min.
    for word in crowd(60, per_minute=30):
        hall.tick(people=3, word=word)
    assert hall.baskets(), "the group never got a paid ad"
    assert hall.swaps() <= 4, hall.baskets()


def test_spoken_topics_expire_once_the_mic_goes_quiet(hall: Hall) -> None:
    for _ in range(6):
        hall.tick(people=1)
    unprompted = hall.baskets()[-1]
    for word in crowd(20, per_minute=30):
        hall.tick(people=1, word=word)
    assert hall.baskets()[-1] != unprompted or hall.swaps() > 0  # speech did move the ad
    for _ in range(int(75 / TICK.total_seconds())):  # TTL is 60 s; nobody says anything
        hall.tick(people=1)
    assert hall.baskets()[-1] == unprompted


def test_a_speech_flood_never_airs_an_age_restricted_brand_to_a_minor(hall: Hall) -> None:
    assert AGE_RESTRICTED, "the catalog has no age-restricted brand to guard"
    for word in crowd(60, per_minute=40, seed=7):
        hall.tick(people=2, minor=True, word=word)
    aired = {brand for basket in hall.baskets() for brand in basket}
    assert aired, "the minor's group never got a paid ad"
    assert not aired & AGE_RESTRICTED, aired & AGE_RESTRICTED
