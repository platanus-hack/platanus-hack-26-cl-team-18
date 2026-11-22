"""Campaign tagging eval (Chile): who can reach each brand, and does what airs fit?

    uv run python -m evals tags      (make eval-tags)  -> evals/results/tags.md

Three views of one question, each a gate:

1. Reach matrix: per brand, the camera labels (vision cues and the market's cue
   overrides), the printed marker (and a marker cue where the camera is blind), the
   spoken intents (keyterm segments) and the photo that reach it. Every brand needs a
   camera label or a marker cue, a printed marker and a photo.
2. Camera sweep: every label the perception agent emits, at five venue dayparts, through
   the real loop (agent socket -> classifier -> auction -> creative -> player). The hook
   must fit the winner's aisle (no "Pilas bajas" on a thirsty shopper) and the photo must
   be one of the Chile campaign shots.
3. Voice sweep: every intent the Chile screen answers by voice, spoken as the corpus
   phrase (evals/fixtures/speech/keyterms_es_cl.json): the winner must sell that aisle.

The loop: a rehearsal shows a wrong ad for a product or a phrase -> it shows up here as a
failed row -> fix the tag, cue, angle or hook -> the eval goes green -> commit.
"""

from __future__ import annotations

import ast
import json
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from app.bootstrap.container import build_container
from app.bootstrap.demo import DEMO_SCREEN, _brand_logo, keyterm_segments
from app.bootstrap.demo_catalog_chile import CHILE
from app.config.settings import AppSettings
from app.contract.messages import (
    AudienceSnapshot,
    AudienceUtterance,
    DetectedObject,
    DistanceBand,
    MinorLikely,
    Tracklet,
)
from app.contract.speech_matching import DEFAULT_KEYTERMS
from app.main import create_app
from app.modules.common.infrastructure.clock.fixed_clock import FixedClock
from app.modules.creative.application.campaigns import media_for
from app.modules.creative.infrastructure.campaign_assets import load_campaign_media

ROOT = Path(__file__).resolve().parents[1]
IAB = json.loads(
    (ROOT / "app/modules/audience/infrastructure/catalog/iab_audience_1_1_cstore.json").read_text()
)
MARKERS = json.loads((ROOT / "edge/props/markers-chile.json").read_text())
CORPUS = json.loads((ROOT / "evals/fixtures/speech/keyterms_es_cl.json").read_text())["cases"]
BRANDS = {brand.slug: brand for brand in CHILE.brands}
CATEGORY = {brand.name: brand.category for brand in CHILE.brands}
DAYPARTS = {"night": 5, "morning": 11, "midday": 16, "afternoon": 19, "evening": 23}  # UTC
# A hook word and the aisles that can honestly answer it. A winner outside the set is a
# messaging mismatch, whatever the auction thought.
FIT: dict[str, set[str]] = {
    "café": {"coffee"},
    "entren": {"energy_drink", "sports_drink", "water"},
    "pilas": {"energy_drink", "sports_drink", "coffee", "energy_pack"},
    "sed": {"water", "sports_drink"},
    "cero alcohol": {"beer_zero"},
    "frío": {"ice_cream", "water", "cola", "juice", "dairy", "sports_drink"},
    "duermes": {"energy_pack", "energy_drink", "coffee"},
}


def _edge_labels() -> list[str]:
    source = (ROOT / "edge/agent/vision/vocabulary.py").read_text()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == (
            "OBJECT_VOCABULARY"
        ):
            assert node.value is not None
            return sorted({label for _, label in ast.literal_eval(node.value)})
    raise AssertionError("OBJECT_VOCABULARY not found")


def reach_matrix() -> tuple[list[dict[str, Any]], list[str]]:
    emitted = set(_edge_labels())
    overrides = dict(CHILE.cue_segments)
    camera: dict[str, set[str]] = {}
    for cue in IAB["cues"]["vision"]:
        if cue["cue"] in emitted:
            camera.setdefault(overrides.get(cue["cue"], cue["segment"]), set()).add(cue["cue"])
    for label, segment in CHILE.cue_segments:
        if label in emitted:
            camera.setdefault(segment, set()).add(label)
    spoken: dict[str, set[str]] = {}
    for word, segment in keyterm_segments(CHILE).items():
        spoken.setdefault(segment, set()).add(word)
    marker_of = {slug: marker for marker, slug in MARKERS.items()}
    marker_cues = dict(CHILE.marker_cues)
    media = load_campaign_media()
    rows, problems = [], []
    for brand in CHILE.brands:
        labels = sorted({label for s in brand.segments for label in camera.get(s, ())})
        words = sorted({word for s in brand.segments for word in spoken.get(s, ())})
        found = media_for(media, ((brand.slug, brand.category),))
        photo = found[1].photo_urls[0].rsplit("/", 1)[-1] if found else ""
        logo = _brand_logo(brand.slug, brand.name, brand.color)
        row = {
            "brand": brand.name,
            "aisle": brand.category,
            "camera": labels,
            "marker": marker_of.get(brand.slug, ""),
            "marker_cue": brand.slug in marker_cues,
            "voice": words,
            "photo": photo,
            "logo": "official" if logo.startswith("/campaign-assets/") else "wordmark",
        }
        rows.append(row)
        if not labels and not row["marker_cue"]:
            problems.append(f"{brand.name}: no camera label and no marker cue reach it")
        if not row["marker"]:
            problems.append(f"{brand.name}: no printed marker")
        if not photo.startswith("cl-"):
            problems.append(f"{brand.name}: no Chile campaign photo ({photo or 'none'})")
    return rows, problems


def _settings(tmp: Path) -> AppSettings:
    options: dict[str, Any] = {
        "_env_file": None,
        "app_env": "test",
        "demo_simulator": True,
        "demo_catalog": "chile",
        "media_dir": tmp,
        "speech_mode_screens": [DEMO_SCREEN],
        "anthropic_api_key": None,
        "elevenlabs_api_key": None,
        "fal_key": None,
        "audience_ai": False,
        "creative_ai_copy": False,
        "voiceovers_enabled": False,
        "vlm_enabled": False,
    }
    return AppSettings(**options)


def _air(
    tmp: Path, hour: int, *, labels: list[str], people: int, text: str | None = None
) -> dict[str, Any]:
    clock = FixedClock(datetime(2026, 11, 21, hour, tzinfo=UTC))
    container = build_container(_settings(tmp), clock=clock)
    with (
        TestClient(create_app(container=container)) as client,
        client.websocket_connect(f"/ws/screen/{DEMO_SCREEN}") as player,
        client.websocket_connect("/ws/agent") as agent,
    ):
        player.receive_json()
        agent.send_text(
            AudienceSnapshot(
                screen_id=DEMO_SCREEN,
                seq=1,
                measured_at=clock.now_utc(),
                people=people,
                looking=people,
                tracklets=[
                    Tracklet(id=f"t{i}", dwell_s=1.0, looking=True, distance_band=DistanceBand.NEAR)
                    for i in range(people)
                ],
                objects=[DetectedObject(label=label, score=1) for label in labels],
                minor_likely=MinorLikely(value=False, confidence=1),
            ).model_dump_json()
        )
        if text is not None:
            agent.send_text(
                AudienceUtterance(
                    screen_id=DEMO_SCREEN,
                    seq=2,
                    measured_at=clock.now_utc(),
                    text=text,
                    language="es",
                    keyterms_hit=list(DEFAULT_KEYTERMS.understand(text).wanted),
                    utterance_id="tags",
                ).model_dump_json()
            )
            time.sleep(0.3)
            assert client.portal is not None
            client.portal.call(container.warm.drain)
        with client.websocket_connect(f"/ws/screen/{DEMO_SCREEN}") as fresh:
            shown = fresh.receive_json()
    spec = shown["spec"]
    brands = [slot["brand_name"] for slot in spec["slots"]] if shown.get("auction_id") else []
    teaser = next((s["copy_text"] for s in spec["stages"] if s["name"] == "teaser"), "")
    photo = (spec["slots"][0].get("hero_image_url") or "").rsplit("/", 1)[-1] if brands else ""
    return {"brands": brands, "teaser": teaser, "photo": photo}


def _fit_problem(aired: dict[str, Any]) -> str | None:
    aisles = {CATEGORY[name] for name in aired["brands"] if name in CATEGORY}
    hook = aired["teaser"].lower()
    for word, fits in FIT.items():
        if word in hook and not aisles & fits:
            return f"hook {aired['teaser']!r} does not fit {sorted(aisles)}"
    if aired["brands"] and not aired["photo"].startswith("cl-"):
        return f"photo {aired['photo'] or 'none'} is not a Chile campaign shot"
    return None


def camera_sweep(tmp: Path) -> tuple[list[dict[str, Any]], list[str]]:
    rows, problems = [], []
    for label in _edge_labels():
        for daypart, hour in DAYPARTS.items():
            aired = _air(tmp, hour, labels=[label], people=2)
            issue = _fit_problem(aired)
            rows.append({"label": label, "daypart": daypart, **aired, "issue": issue or ""})
            if issue:
                problems.append(f"camera {label} @ {daypart}: {issue}")
    return rows, problems


def voice_sweep(tmp: Path) -> tuple[list[dict[str, Any]], list[str]]:
    segment_of = keyterm_segments(CHILE)
    phrase_for: dict[str, str] = {}
    for case in CORPUS:
        if len(case["wanted"]) == 1 and not case["rejected"]:
            phrase_for.setdefault(case["wanted"][0], case["text"])
    rows, problems = [], []
    for intent, segment in sorted(segment_of.items()):
        text = phrase_for.get(intent)
        if text is None:
            problems.append(f"voice {intent}: no corpus phrase")
            continue
        aired = _air(tmp, DAYPARTS["midday"], labels=[], people=1, text=text)
        sellers = {b.name for b in CHILE.brands if segment in b.segments}
        issue = _fit_problem(aired)
        if not set(aired["brands"]) & sellers:
            issue = f"aired {aired['brands'] or 'house ad'}, none of them sells {segment}"
        rows.append({"intent": intent, "phrase": text, **aired, "issue": issue or ""})
        if issue:
            problems.append(f"voice {intent!r} ({text!r}): {issue}")
    return rows, problems


def render(
    matrix: list[dict[str, Any]], camera: list[dict], voice: list[dict], problems: list[str]
) -> str:
    out = [
        f"# Campaign tagging (Chile): {'PASS' if not problems else 'FAIL'}",
        "",
        f"{len(matrix)} brands, {len(camera)} camera scenes, {len(voice)} spoken intents.",
        "",
        "## Reach matrix",
        "",
        "| Brand | Aisle | Camera labels | Marker | Voice | Photo | Logo |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in matrix:
        marker = f"{r['marker']}{' (cue)' if r['marker_cue'] else ''}"
        out.append(
            f"| {r['brand']} | {r['aisle']} | {', '.join(r['camera']) or '-'} | {marker or '-'} "
            f"| {', '.join(r['voice']) or '-'} | {r['photo'] or '-'} | {r['logo']} |"
        )
    out += ["", "## Voice", "", "| Intent | Phrase | Airs | Hook | |", "|---|---|---|---|---|"]
    for r in voice:
        mark = "❌ " + r["issue"] if r["issue"] else "✅"
        out.append(
            f"| {r['intent']} | {r['phrase']} | {' + '.join(r['brands']) or 'house ad'} "
            f"| {r['teaser']} | {mark} |"
        )
    out += ["", "## Camera", "", "| Label | Daypart | Airs | Hook | |", "|---|---|---|---|---|"]
    for r in camera:
        mark = "❌ " + r["issue"] if r["issue"] else "✅"
        out.append(
            f"| {r['label']} | {r['daypart']} | {' + '.join(r['brands']) or 'house ad'} "
            f"| {r['teaser']} | {mark} |"
        )
    if problems:
        out += ["", "## Problems", "", *(f"- {p}" for p in problems)]
    return "\n".join(out) + "\n"


def main(results_dir: Path | None = None) -> int:
    matrix, problems = reach_matrix()
    with tempfile.TemporaryDirectory(prefix="evals-tags-") as tmp:
        camera, camera_problems = camera_sweep(Path(tmp))
        voice, voice_problems = voice_sweep(Path(tmp))
    problems += voice_problems + camera_problems
    text = render(matrix, camera, voice, problems)
    target = (results_dir or ROOT / "evals/results") / "tags.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    print(text.split("## Camera")[0])
    print(f"-> {target.relative_to(ROOT) if target.is_relative_to(ROOT) else target}")
    for problem in problems:
        print(f"FAIL {problem}")
    return 1 if problems else 0
