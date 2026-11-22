"""The YOLOE store-vocabulary feedback loop: does each prompt find its product?

    cd edge && uv run python -m agent.vision.vocab_eval            # current vocabulary
    uv run python -m agent.vision.vocab_eval --candidate try.json  # a prompt list to try
    uv run python -m agent.vision.vocab_eval --update-baseline     # lock in an improvement

Scores the vocabulary (vocabulary.py) on labelled product photos (evals/vocab/
photos.json): per canonical label, how many photos of it the model finds at the live
confidence (OBJECT_CONF), and how many photos of *other* products get that label (a
false hit sends the moment to the wrong aisle: "milk" on a Monster can). It compares
with the committed baseline and exits 1 when any label loses recall or gains false hits,
so a prompt edit can't silently undo what works. YOLOE gives one class per box, so a
new prompt can steal boxes from an old one: that is exactly what this catches.

A candidate (--candidate) is a JSON list of [prompt, label] pairs, like
OBJECT_VOCABULARY; it needs the text encoder (MobileCLIP, first run downloads it). The
current vocabulary uses the baked weights (make edge-yoloe). Commons photos are fetched
on demand into evals/vocab/.cache/ (gitignored, never committed).

Loop: a rehearsal shows the camera missing a product -> add its photo to photos.json ->
this eval shows the miss -> try prompts with --candidate -> keep one that raises that
label without lowering any other -> edit vocabulary.py (and gpu/service/) ->
--update-baseline -> commit both.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent.vision.vocabulary import (
    DEFAULT_OBJECT_MODEL,
    OBJECT_CONF,
    OBJECT_LABELS,
    OBJECT_VOCABULARY,
    load_object_model,
)

EDGE = Path(__file__).resolve().parents[2]
REPO = EDGE.parent
SET_DIR = EDGE / "evals" / "vocab"
CACHE = SET_DIR / ".cache"
BASELINE = SET_DIR / "baseline.json"
# Wikimedia's UA policy wants a contact URL; without one it answers 429.
USER_AGENT = "team18-evals/0.1 (hackathon test fixtures; https://github.com/platanus-hack)"


@dataclass
class Score:
    photos: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    found: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    false_hits: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    misses: list[tuple[str, str, list[str]]] = field(default_factory=list)

    def as_json(self) -> dict[str, Any]:
        labels = sorted(set(self.photos) | set(self.false_hits))
        return {
            label: {
                "photos": self.photos.get(label, 0),
                "found": self.found.get(label, 0),
                "false_hits": self.false_hits.get(label, 0),
            }
            for label in labels
        }


def load_set() -> list[dict[str, str]]:
    return list(json.loads((SET_DIR / "photos.json").read_text(encoding="utf-8"))["photos"])


def _api_thumb(title: str) -> str:
    query = urllib.parse.urlencode(
        {
            "action": "query",
            "format": "json",
            "titles": title,
            "prop": "imageinfo",
            "iiprop": "url",
            "iiurlwidth": "800",
        }
    )
    request = urllib.request.Request(
        f"https://commons.wikimedia.org/w/api.php?{query}", headers={"User-Agent": USER_AGENT}
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        pages = json.load(response)["query"]["pages"]
    info = next(iter(pages.values()))["imageinfo"][0]
    return str(info.get("thumburl") or info["url"])


def photo_path(photo: dict[str, str]) -> Path:
    """The local file for a photo, downloading a Commons one once (polite, resumable)."""
    if "repo" in photo:
        return REPO / photo["repo"]
    path = CACHE / f"{photo['id']}.jpg"
    if path.is_file():
        return path
    CACHE.mkdir(parents=True, exist_ok=True)
    for attempt in range(5):
        try:
            url = _api_thumb(photo["commons"])
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=60) as response:
                path.write_bytes(response.read())
            time.sleep(1.5)
            return path
        except Exception as exc:  # 429s and transient errors: back off and retry
            if attempt == 4:
                raise RuntimeError(f"could not fetch {photo['commons']}: {exc}") from exc
            time.sleep(8 * 2**attempt)
    raise AssertionError("unreachable")


def score(model: Any, vocabulary: list[tuple[str, str]], photos: list[dict[str, str]]) -> Score:
    label_for = dict(vocabulary)
    emitted = set(label_for.values())
    result = Score()
    for photo in photos:
        want = photo["label"]
        prediction = model.predict(str(photo_path(photo)), conf=OBJECT_CONF, verbose=False)[0]
        got = {label_for.get(prediction.names[int(c)], "") for c in prediction.boxes.cls.tolist()}
        got.discard("")
        result.photos[want] += 1
        if want in got:
            result.found[want] += 1
        else:
            result.misses.append((photo["id"], want, sorted(got)))
        for wrong in got - {want}:
            # Only labels a person would read as a product count as a false hit; a
            # "cup" seen on a coffee photo that is labelled "pastry" is still right.
            if wrong in emitted and wrong not in {"cup", "bottle", "cell phone", "laptop"}:
                result.false_hits[wrong] += 1
    return result


def regressions(current: dict[str, Any], baseline: dict[str, Any]) -> list[str]:
    problems = []
    for label, before in baseline.items():
        now = current.get(label, {"found": 0, "false_hits": 0, "photos": 0})
        if now["found"] < before["found"]:
            problems.append(f"{label}: found {now['found']} < {before['found']}")
        if now["false_hits"] > before["false_hits"]:
            problems.append(f"{label}: false hits {now['false_hits']} > {before['false_hits']}")
    return problems


def report(name: str, result: Score, baseline: dict[str, Any] | None) -> str:
    lines = [
        f"## {name}",
        "",
        "| label | found | photos | false hits | baseline |",
        "|---|---:|---:|---:|---|",
    ]
    for label, row in result.as_json().items():
        base = (baseline or {}).get(label)
        note = "" if base is None else f"{base['found']}/{base['photos']}, {base['false_hits']} fh"
        tag = "" if label in OBJECT_LABELS else " (not in vocabulary)"
        lines.append(
            f"| {label}{tag} | {row['found']} | {row['photos']} | {row['false_hits']} | {note} |"
        )
    total = sum(result.found.values()), sum(result.photos.values())
    lines += ["", f"found {total[0]}/{total[1]}, false hits {sum(result.false_hits.values())}"]
    if result.misses:
        lines += [
            "",
            "misses: " + "; ".join(f"{i} ({w} -> {g or '-'})" for i, w, g in result.misses),
        ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--candidate", type=Path, help="JSON [[prompt, label], ...] to try")
    parser.add_argument("--update-baseline", action="store_true")
    parser.add_argument("--weights", default=DEFAULT_OBJECT_MODEL)
    args = parser.parse_args(argv)
    photos = load_set()
    baseline = json.loads(BASELINE.read_text()) if BASELINE.is_file() else None

    if args.candidate is not None:
        from ultralytics import YOLOE  # local import: optional dependency

        vocabulary = [tuple(pair) for pair in json.loads(args.candidate.read_text())]
        model = YOLOE(args.weights)
        model.set_classes([prompt for prompt, _ in vocabulary])
        result = score(model, vocabulary, photos)  # type: ignore[arg-type]
        print(report(f"candidate {args.candidate.name}", result, baseline))
        problems = regressions(result.as_json(), baseline or {})
        print("\n" + ("REGRESSES: " + "; ".join(problems) if problems else "no regression"))
        return 0  # a candidate is an experiment, never a gate

    model = load_object_model(args.weights)
    result = score(model, list(OBJECT_VOCABULARY), photos)
    current = result.as_json()
    print(report("current vocabulary", result, baseline))
    problems = regressions(current, baseline or {})
    if args.update_baseline:
        if problems:
            print("\nrefusing to update a baseline that regresses: " + "; ".join(problems))
            return 1
        BASELINE.write_text(json.dumps(current, indent=1, sort_keys=True) + "\n")
        print(f"\nbaseline updated: {BASELINE.relative_to(REPO)}")
        return 0
    if problems:
        print("\nFAIL (vs baseline): " + "; ".join(problems))
        return 1
    print("\nPASS (no label lost recall or gained false hits)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
