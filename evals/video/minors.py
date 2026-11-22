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
import bisect
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
SCORE_HZ = 5  # fixed clip-time samples, independent of heartbeat/change publication
MAX_SAMPLE_AGE_S = 1.5  # one heartbeat plus margin; missing data never proves safety


def score(
    labels: dict[str, list[dict[str, Any]]], *, recordings: Path | None = None
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    child_hits = child_total = adult_flags = adult_total = missed = windows = 0
    protected = safety_samples = unprotected_windows = missing = empty = 0
    uncertain_adults = 0
    missing_samples = 0
    for clip, spans in sorted(labels.items()):
        recording = (recordings or VIDEO_OUT) / clip / "recording.agent.jsonl"
        if not recording.exists():
            missing += 1
            rows.append({"clip": clip, "error": "no recording (run make eval-video)"})
            continue
        samples = [json.loads(line) for line in recording.read_text().splitlines() if line]
        samples.sort(key=lambda sample: sample["t_s"])
        instants = [sample["t_s"] for sample in samples]
        for span in spans:
            # Score what the backend knew at fixed instants. Extra uncertainty
            # transitions must not inflate the denominator and depress recall.
            observed = []
            tick = 0
            while (at := span["start_s"] + SETTLE_S + tick / SCORE_HZ) < span["end_s"]:
                index = bisect.bisect_right(instants, at) - 1
                if index >= 0 and at - instants[index] <= MAX_SAMPLE_AGE_S:
                    observed.append(samples[index]["snapshot"])
                else:
                    observed.append(None)
                    missing_samples += 1
                tick += 1
            if not any(s is not None for s in observed):
                empty += 1
            inside = [s["minor_likely"]["value"] if s is not None else False for s in observed]
            flagged = sum(inside)
            if span["minor"]:
                windows += 1
                child_hits += flagged
                child_total += len(inside)
                missed += int(flagged == 0)
                safety = [
                    s is not None
                    and (
                        s["people"] == 0
                        or s["minor_likely"]["value"]
                        or s["minor_likely"].get("uncertain", False)
                    )
                    for s in observed
                ]
                protected += sum(safety)
                safety_samples += len(safety)
                unprotected_windows += int(not safety or not all(safety))
            else:
                adult_flags += flagged
                adult_total += len(inside)
                uncertain_adults += sum(
                    s["minor_likely"].get("uncertain", False)
                    for s in observed
                    if s is not None and s["people"] > 0
                )
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
        "minor_sample_protection": protected / safety_samples if safety_samples else None,
        "minor_windows_unprotected": unprotected_windows,
        "minor_recordings_missing": missing,
        "minor_windows_without_samples": empty,
        "minor_samples_missing": missing_samples,
        "minor_uncertain_adult_share": uncertain_adults / adult_total if adult_total else None,
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
