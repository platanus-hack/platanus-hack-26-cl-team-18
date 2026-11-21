"""Eval CLI. See docs/evals.md.

python -m evals                      scripted scenarios + soak → scorecard (CI gate)
python -m evals --update-baseline    lock in improved ratchet metrics
python -m evals replay REC.jsonl     real-video perception recording → live backend
python -m evals perception LABELS REC.jsonl   score the agent against hand labels
python -m evals record --out FILE    record a live rehearsal from a running backend
python -m evals rehearsal FILE       score a recorded rehearsal
python -m evals creative [--writer stock|recorded|claude]   creative generation eval
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

    creative = sub.add_parser(
        "creative", help="creative generation eval (docs/creative-generation.md)"
    )
    creative.add_argument("--writer", choices=["stock", "recorded", "claude"], default="recorded")
    creative.add_argument("--record", action="store_true", help="live: overwrite recordings.json")
    creative.add_argument("--update-baseline", dest="creative_baseline", action="store_true")
    sub.add_parser("campaigns", help="campaign catalog, media, safety, quality and latency")
    sub.add_parser("speech", help="speech event → auction → player regression suite")

    pipeline = sub.add_parser(
        "pipeline", help="end-to-end ad pipeline journeys: stage waterfall + bottleneck"
    )
    pipeline.add_argument("--dwell", type=float, default=5.0, help="seconds per moment")
    pipeline.add_argument("--copy-scale", type=float, default=1.0, help="x recorded Claude latency")
    pipeline.add_argument("--tts-latency-ms", type=float, default=350.0)
    pipeline.add_argument("--cold", action="store_true", help="also run a cold-boot journey")

    browser = sub.add_parser(
        "browser", help="glass-to-glass in headless Chromium (needs: uv run --with playwright)"
    )
    browser.add_argument("--moments", type=int, default=21)
    browser.add_argument("--dwell", type=float, default=1.2)
    browser.add_argument("--reuse-build", action="store_true")

    warmup = sub.add_parser(
        "warmup", help="demo-day: warm copy + voice caches on a running backend"
    )
    warmup.add_argument("--server", default="ws://127.0.0.1:8000")
    warmup.add_argument("--scenes", nargs="*", help="scene names (default: the demo journey)")

    args = parser.parse_args(argv)
    command = args.command or "suite"
    if command == "campaigns":
        from evals.campaigns import main as campaign_eval

        return campaign_eval(args.out)
    if command == "speech":
        from evals.speech import run_speech_scenarios

        runs = run_speech_scenarios()
        decisions = [d for run in runs for d in run.decisions]
        checks = [c for run in runs for c in run.checks]
        violations = m.invariant_violations(decisions)
        failures = [
            f"{c.source} step {c.step}: {c.name}: {c.detail}" for c in checks if not c.passed
        ]
        args.update_baseline = False
        return _finish(
            args,
            "speech",
            {
                "scenario_checks_failed": len(failures),
                "invariant_violations": len(violations),
                **m.latency_kpis(decisions),
            },
            [_section(run, "Speech → player") for run in runs],
            violations,
            failures,
        )
    if command == "suite":
        return _run_suite(args)
    if command == "pipeline":
        return _run_pipeline(args)
    if command == "warmup":
        return _run_warmup(args)
    if command == "browser":
        return _run_browser(args)
    if command == "replay":
        return _run_replay(args)
    if command == "perception":
        return _run_perception(args)
    if command == "creative":
        return _run_creative(args)
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
    *,
    extra_markdown: str = "",
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
    markdown = sc.render_markdown(report, history) + extra_markdown
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


def _run_pipeline(args: argparse.Namespace) -> int:
    from evals import pipeline as pl

    modes = [("gen", False, False), ("demo", False, True)] + (
        [("cold", True, False)] if args.cold else []
    )
    metrics: dict[str, Any] = {}
    extra = ""
    for prefix, cold, warmup in modes:
        run = pl.run_journey(
            dwell_s=args.dwell,
            copy_scale=args.copy_scale,
            tts_latency_ms=args.tts_latency_ms,
            cold=cold,
            warmup=warmup,
        )
        analysis = pl.analyse(run)
        title = {"gen": "Generation mode", "demo": "Demo mode (warmed)", "cold": "Cold boot"}[
            prefix
        ]
        extra += f"\n# {title}\n\n" + pl.render(run, analysis)
        metrics.update({f"{prefix}_{k}": v for k, v in analysis["metrics"].items()})
        metrics[f"{prefix}_bottleneck"] = analysis["bottleneck"]
    gates = {
        "house_ad_billed": sum(metrics.get(f"{p}_house_ad_billed", 0) or 0 for p, _, _ in modes),
        "voice_duplicate_syntheses": sum(
            metrics.get(f"{p}_voice_duplicate_syntheses", 0) or 0 for p, _, _ in modes
        ),
    }
    metrics.update(gates)
    args.update_baseline = False
    return _finish(args, "pipeline", metrics, [], [], [], extra_markdown=extra)


def _run_browser(args: argparse.Namespace) -> int:
    from evals import browser as br

    run = br.run_browser(moments=args.moments, dwell_s=args.dwell, reuse_build=args.reuse_build)
    analysis = br.analyse(run)
    (br.OUT / "moments.json").write_text(br.to_json(run), encoding="utf-8")
    args.update_baseline = False
    return _finish(
        args,
        "browser",
        analysis["metrics"],
        [],
        [],
        [],
        extra_markdown="\n" + br.render(run, analysis),
    )


def _run_warmup(args: argparse.Namespace) -> int:
    from evals import pipeline as pl
    from evals.live import LiveBackend
    from evals.scenario import load_scenes

    scenes = load_scenes()
    names = args.scenes or list(dict.fromkeys(pl.JOURNEY))
    server = args.server.rstrip("/")
    results = pl.warm_up(LiveBackend(http_url="", ws_url=server), [scenes[n] for n in names])
    for r in results:
        if not r.paid:
            state = "house ad (nothing to warm)"
        else:
            voice = "voice-over cached" if r.voice_ms is not None else "NO voice-over"
            copy = "AI copy cached" if r.copy_ms is not None else "stock copy (AI copy off/slow)"
            state = f"{voice} · {copy}"
        print(f"{r.scene:16} {state}")
    missing = [r.scene for r in results if r.paid and r.voice_ms is None]
    if missing:
        print(f"not warmed (check ElevenLabs config): {', '.join(missing)}")
    return 1 if missing else 0


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


def _run_creative(args: argparse.Namespace) -> int:
    import anyio

    from evals.creative import runner

    report = anyio.run(lambda: runner.run(args.writer))
    rows = runner.judge(report.metrics, runner.load_baseline(report.writer), writer=report.writer)
    latest = runner.write_results(report, runner.render(report, rows), args.out)
    print(latest.read_text(encoding="utf-8"))
    if args.record and report.writer == "claude":
        runner.save_recordings(report)
        print(f"recorded {len(report.recorded)} responses to {runner.RECORDINGS_FILE}")
    bad = runner.failed(rows)
    if args.creative_baseline:
        if any(r["status"] == "FAIL" for r in rows):
            print("refusing to update the baseline: a gate failed")
            return 1
        runner.save_baseline(report)
        print(f"baseline updated for `{report.writer}` in {runner.BASELINE_FILE}")
        return 0
    if bad:
        print("FAILED: " + ", ".join(bad))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
