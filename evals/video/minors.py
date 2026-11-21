"""Minor-safety on real footage: the hand-labelled frames vs the agent's minor_likely flag.

    uv run python -m evals.video.minors              # after `make eval-video`
    uv run python -m evals.video.minors --update-baseline

Invariant 2: `minor_likely` only *blocks* age-restricted ads. A miss leaves the warm
VLM as the second line; a false flag blocks beer for adults. Both are measured here so
the pose heuristic (edge/agent/vision/minor.py) is never retuned blind:

* minor_sample_recall: share of agent snapshots inside child windows that flag a minor
* minor_windows_unflagged: child windows with no flagged snapshot at all
* minor_false_flag_rate: share of snapshots inside adult-only windows that flag one

All three are ratchets (evals/video/minor_baseline.json): today's recall is weak on
close-ups and carried toddlers, and the number may only go up from here.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from evals import scorecard as sc
from evals.video.run import OUT as VIDEO_OUT

HERE = Path(__file__).resolve().parent
LABELS = HERE / "minor_labels.json"
BASELINE = HERE / "minor_baseline.json"
SETTLE_S = 0.5  # the agent's vote needs a few frames before a window is scored


def score(labels: dict[str, list[dict[str, Any]]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    child_hits = child_total = adult_flags = adult_total = missed = windows = 0
    for clip, spans in sorted(labels.items()):
        recording = VIDEO_OUT / clip / "recording.agent.jsonl"
        if not recording.exists():
            rows.append({"clip": clip, "error": "no recording (run make eval-video)"})
            continue
        samples = [json.loads(line) for line in recording.read_text().splitlines() if line]
        for span in spans:
            inside = [
                s["snapshot"]["minor_likely"]["value"]
                for s in samples
                if span["start_s"] + SETTLE_S <= s["t_s"] < span["end_s"]
                and s["snapshot"]["people"] > 0
            ]
            flagged = sum(inside)
            if span["minor"]:
                windows += 1
                child_hits += flagged
                child_total += len(inside)
                missed += int(inside != [] and flagged == 0)
            else:
                adult_flags += flagged
                adult_total += len(inside)
            rows.append(
                {
                    "clip": clip,
                    "window": f"{span['start_s']}–{span['end_s']}s",
                    "minor": span["minor"],
                    "flagged": f"{flagged}/{len(inside)}",
                    "note": span.get("note", ""),
                }
            )
    metrics = {
        "minor_sample_recall": child_hits / child_total if child_total else None,
        "minor_windows_unflagged": missed,
        "minor_windows_labelled": windows,
        "minor_false_flag_rate": adult_flags / adult_total if adult_total else None,
    }
    return metrics, rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--update-baseline", action="store_true")
    args = parser.parse_args(argv)
    labels = json.loads(LABELS.read_text(encoding="utf-8"))["clips"]
    metrics, rows = score(labels)
    verdicts = sc.judge(metrics, sc.load_baseline(BASELINE))
    print("| clip | window | child? | flagged | note |\n|---|---|:-:|---:|---|")
    for row in rows:
        if "error" in row:
            print(f"| {row['clip']} | — | — | — | {row['error']} |")
            continue
        mark = "child" if row["minor"] else "adults"
        print(f"| {row['clip']} | {row['window']} | {mark} | {row['flagged']} | {row['note']} |")
    print()
    for v in verdicts:
        label = sc.SPECS[v.metric].label if v.metric in sc.SPECS else v.metric
        print(f"{v.status:>8}  {label}: {v.value} (baseline {v.baseline})")
    if args.update_baseline:
        if any(v.status == "fail" for v in verdicts):
            print("refusing to update the baseline from a failing run", file=sys.stderr)
            return 1
        sc.write_baseline(metrics, BASELINE)
        print(f"baseline updated: {BASELINE}", file=sys.stderr)
    return 1 if sc.failed(verdicts) else 0


if __name__ == "__main__":
    raise SystemExit(main())
