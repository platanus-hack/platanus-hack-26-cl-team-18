"""Declarative scenarios: a timeline of audience scenes and what the screen must do.

Scenario files live in evals/scenarios/*.json. Scenes are reusable by name from
evals/scenarios/scenes.json or written inline. Adding a demo moment is a data
change, not a code change.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from app.contract.messages import AudienceSnapshot, DetectedObject, MinorLikely, SoundScene

SCENARIO_DIR = Path(__file__).resolve().parent / "scenarios"
SCENES_FILE = SCENARIO_DIR / "scenes.json"


@dataclass(frozen=True, slots=True, kw_only=True)
class Scene:
    """What the perception agent would report for one instant (structured facts only)."""

    name: str
    people: int
    looking: int
    objects: dict[str, float] = field(default_factory=dict)
    sound: dict[str, float] = field(default_factory=dict)
    minor: bool = False

    def snapshot(self, *, screen_id: str, seq: int, measured_at: datetime) -> AudienceSnapshot:
        return AudienceSnapshot(
            screen_id=screen_id,
            seq=seq,
            measured_at=measured_at,
            people=self.people,
            looking=self.looking,
            objects=[DetectedObject(label=k, score=v) for k, v in sorted(self.objects.items())],
            sound_scene=[SoundScene(label=k, score=v) for k, v in sorted(self.sound.items())],
            minor_likely=MinorLikely(value=self.minor, confidence=1),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class Expect:
    """Expected screen outcome after one step. Unset fields are not checked."""

    no_change: bool = False
    brands: frozenset[str] | None = None  # empty set = house ad
    layout: str | None = None
    excluded: dict[str, str] = field(default_factory=dict)  # brand -> reason
    min_coverage: float | None = None
    copy_contains: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class Step:
    scene: Scene
    expect: Expect
    measured_offset: timedelta = timedelta(0)  # negative = stale, positive = device clock ahead
    note: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class Scenario:
    name: str
    beat: str
    description: str
    steps: tuple[Step, ...]


def load_scenes(path: Path = SCENES_FILE) -> dict[str, Scene]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {name: _scene(name, body) for name, body in raw.items() if not name.startswith("_")}


def load_scenarios(
    directory: Path = SCENARIO_DIR, *, only: set[str] | None = None
) -> tuple[Scenario, ...]:
    scenes = load_scenes()
    scenarios = []
    for path in sorted(directory.glob("*.json")):
        if path == SCENES_FILE:
            continue
        scenario = parse_scenario(json.loads(path.read_text(encoding="utf-8")), scenes)
        if only is None or scenario.name in only:
            scenarios.append(scenario)
    return tuple(scenarios)


def parse_scenario(raw: dict[str, Any], scenes: dict[str, Scene]) -> Scenario:
    steps: list[Step] = []
    for index, body in enumerate(raw["steps"]):
        scene_ref = body["scene"]
        if isinstance(scene_ref, str):
            if scene_ref not in scenes:
                raise ValueError(f"{raw['name']} step {index}: unknown scene {scene_ref!r}")
            scene = scenes[scene_ref]
        else:
            scene = _scene(f"{raw['name']}#{index}", scene_ref)
        step = Step(
            scene=scene,
            expect=_expect(body.get("expect", {})),
            measured_offset=timedelta(seconds=float(body.get("measured_offset_s", 0))),
            note=body.get("note", ""),
        )
        steps.append(step)
        # `hold: n` repeats the scene n times and expects the screen to stay put:
        # a group standing still must never re-auction (debounce, no flicker).
        steps.extend(
            Step(scene=scene, expect=Expect(no_change=True), note="hold")
            for _ in range(int(body.get("hold", 0)))
        )
    return Scenario(
        name=raw["name"],
        beat=raw.get("beat", ""),
        description=raw.get("description", ""),
        steps=tuple(steps),
    )


def _scene(name: str, body: dict[str, Any]) -> Scene:
    return Scene(
        name=body.get("name", name),
        people=int(body["people"]),
        looking=int(body.get("looking", body["people"])),
        objects={str(k): float(v) for k, v in body.get("objects", {}).items()},
        sound={str(k): float(v) for k, v in body.get("sound", {}).items()},
        minor=bool(body.get("minor", False)),
    )


def _expect(body: dict[str, Any]) -> Expect:
    unknown = set(body) - {
        "no_change",
        "brands",
        "layout",
        "excluded",
        "min_coverage",
        "copy_contains",
    }
    if unknown:
        raise ValueError(f"unknown expectation keys: {sorted(unknown)}")
    return Expect(
        no_change=bool(body.get("no_change", False)),
        brands=frozenset(body["brands"]) if "brands" in body else None,
        layout=body.get("layout"),
        excluded=dict(body.get("excluded", {})),
        min_coverage=body.get("min_coverage"),
        copy_contains=body.get("copy_contains"),
    )
