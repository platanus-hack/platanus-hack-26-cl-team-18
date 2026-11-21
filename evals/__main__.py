"""Eval CLI. See docs/evals.md.

python -m evals                      scripted scenarios + soak → scorecard (CI gate)
python -m evals --update-baseline    lock in improved ratchet metrics
python -m evals replay REC.jsonl     real-video perception recording → live backend
python -m evals perception LABELS REC.jsonl   score the agent against hand labels
python -m evals record --out FILE    record a live rehearsal from a running backend
python -m evals rehearsal FILE       score a recorded rehearsal
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

from evals import metrics as m, scorecard as sc
from evals.harness import RunRecord, replay_recording, run_scenario, run_soak
from evals.perception import load_labels, load_recording, score_clip
from evals.rehearsal import decisions_from_recording, record
from evals.scenario import load_scenarios, load_scenes

SOAK_SCENES = (
    "empty",
    "co-brand",
    "co-brand-minor",
    "fitness",
    "coffee",
    "adult",
    "minor-safe",
    "low-confidence",
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m evals",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--out", type=Path, default=sc.RESULTS_DIR, help="results directory")
    sub = parser.add_subparsers(dest="command")

    suite = sub.add_parser("suite", help="scripted scenarios + soak (default)")
    _suite_args(suite)
    _suite_args(parser)

    replay = sub.add_parser(
        "replay", help="replay a recorded perception JSONL through the live backend"
    )
    replay.add_argument("recording", type=Path)
    replay.add_argument("--speed", type=float, default=1.0)

    perception = sub.add_parser("perception", help="score agent snapshots against hand labels")
    perception.add_argument("labels", type=Path)
    perception.add_argument("recording", type=Path)

    rec = sub.add_parser("record", help="record a live rehearsal (Ctrl-C to stop)")
    rec.add_argument("--server", default="ws://127.0.0.1:8000")
    rec.add_argument("--out", dest="record_out", type=Path, required=True)
    rec.add_argument("--duration", type=float, default=None)

    rehearsal = sub.add_parser("rehearsal", help="score a recorded rehearsal")
    rehearsal.add_argument("recording", type=Path)

    args = parser.parse_args(argv)
    command = args.command or "suite"
    if command == "suite":
        return _run_suite(args)
    if command == "replay":
        return _run_replay(args)
    if command == "perception":
        return _run_perception(args)
    if command == "record":
        count = record(args.server, args.record_out, duration_s=args.duration)
        print(f"recorded {count} messages to {args.record_out}")
        return 0
    return _run_rehearsal(args)


def _suite_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--only", action="append", help="run only this scenario (repeatable)")
    parser.add_argument("--soak", type=int, default=200, help="soak steps (0 disables)")
    parser.add_argument("--seed", type=int, default=18)
    parser.add_argument("--update-baseline", action="store_true")
    parser.add_argument("--no-save", action="store_true", help="don't write results/")


def _run_suite(args: argparse.Namespace) -> int:
    scenarios = load_scenarios(only=set(args.only) if args.only else None)
    runs = [run_scenario(scenario) for scenario in scenarios]
    beats = {scenario.name: scenario.beat for scenario in scenarios}
    soak: RunRecord | None = None
    if args.soak:
        scenes = load_scenes()
        soak = run_soak([scenes[name] for name in SOAK_SCENES], steps=args.soak, seed=args.seed)

    decisions = [d for run in runs for d in run.decisions]
    checks = [c for run in runs for c in run.checks]
    timed = decisions + (soak.decisions if soak else [])
    violations = m.invariant_violations(timed)
    metrics: dict[str, Any] = {
        "scenario_checks_failed": sum(not c.passed for c in checks),
        "scenario_pass_rate": _ratio(
            sum(all(c.passed for c in run.checks) for run in runs), len(runs)
        ),
        "invariant_violations": len(violations),
        **m.product_kpis(decisions),
        **m.latency_kpis(timed),
    }
    if soak:
        metrics["soak_errors"] = len(soak.errors)
        # No flicker metric here: the soak's random walk swaps scenes by design.
        # Flicker is measured on real-time replays and rehearsals.
        metrics["soak_decisions_per_s"] = (
            len(soak.decisions) / soak.duration_s if soak.duration_s else None
        )
    failures = [
        f"`{c.source}` step {c.step} · {c.name}: {c.detail}" for c in checks if not c.passed
    ]
    if soak:
        failures += [f"soak · {error}" for error in soak.errors]
    sections = [_section(run, beats.get(run.source, "")) for run in runs]
    if soak:
        sections.append(_section(soak, "Stability + latency under load"))
    suite_name = "scenarios" if not args.only else "scenarios:" + ",".join(sorted(args.only))
    return _finish(args, suite_name, metrics, sections, violations, failures)


def _run_replay(args: argparse.Namespace) -> int:
    run = replay_recording(args.recording, speed=args.speed)
    violations = m.invariant_violations(run.decisions)
    metrics: dict[str, Any] = {
        "invariant_violations": len(violations),
        **m.product_kpis(run.decisions),
        **m.latency_kpis(run.decisions),
        "flicker_per_minute": m.flicker_per_minute(run.decisions, duration_s=run.duration_s),
    }
    args.update_baseline = False
    return _finish(
        args,
        f"replay:{args.recording.name}",
        metrics,
        [_section(run, "Real video → live backend")],
        violations,
        [],
    )


def _run_perception(args: argparse.Namespace) -> int:
    metrics = score_clip(load_labels(args.labels), load_recording(args.recording))
    args.update_baseline = False
    return _finish(args, f"perception:{args.labels.stem}", metrics, [], [], [])


def _run_rehearsal(args: argparse.Namespace) -> int:
    decisions, duration = decisions_from_recording(args.recording)
    violations = m.invariant_violations(decisions)
    metrics: dict[str, Any] = {
        "invariant_violations": len(violations),
        **m.product_kpis(decisions),
        **m.latency_kpis(decisions),
        "flicker_per_minute": m.flicker_per_minute(decisions, duration_s=duration),
    }
    args.update_baseline = False
    return _finish(args, f"rehearsal:{args.recording.name}", metrics, [], violations, [])


def _finish(
    args: argparse.Namespace,
    suite: str,
    metrics: dict[str, Any],
    sections: list[dict[str, Any]],
    violations: list[m.Violation],
    failures: list[str],
) -> int:
    baseline = sc.load_baseline() if suite == "scenarios" else {}
    verdicts = sc.judge(metrics, baseline)
    report = sc.build_report(
        suite=suite,
        metrics=metrics,
        verdicts=verdicts,
        sections=sections,
        violations=violations,
        failures=failures,
    )
    history = sc.load_history(args.out, suite=suite) + [report]
    markdown = sc.render_markdown(report, history)
    if not getattr(args, "no_save", False):
        path = sc.save_run(report, markdown, args.out)
        print(f"results: {path}", file=sys.stderr)
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(markdown + "\n")
    print(markdown)
    if getattr(args, "update_baseline", False):
        if failures or violations or any(v.status == "fail" for v in verdicts):
            print("refusing to update the baseline from a failing run", file=sys.stderr)
            return 1
        sc.write_baseline(metrics)
        print(f"baseline updated: {sc.BASELINE_FILE}", file=sys.stderr)
        return 0
    return 1 if sc.failed(verdicts) else 0


def _section(run: RunRecord, beat: str) -> dict[str, Any]:
    return {
        "name": run.source,
        "beat": beat,
        "checks": len(run.checks),
        "checks_failed": sum(not c.passed for c in run.checks),
        "decisions": len(run.decisions),
        "duration_s": run.duration_s,
        "errors": run.errors,
    }


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


if __name__ == "__main__":
    sys.exit(main())
