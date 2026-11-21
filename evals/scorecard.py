"""Gates, baseline ratchet, run history and the Markdown scorecard.

Three kinds of metric:

* **gate**: an absolute bound that never moves (invariant violations = 0,
  server decision p95 <= 30 ms). Failing one fails the run.
* **ratchet**: compared with evals/baseline.json in its declared direction.
  Worse than baseline fails the run; better passes and asks for
  `make eval-baseline` so the gain is locked in.
* **info**: tracked in history and shown with its delta, never fails
  (wall-clock latency varies by machine, so it is gated absolutely, not
  against a baseline).
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from evals import metrics as m

EVALS_DIR = Path(__file__).resolve().parent
BASELINE_FILE = EVALS_DIR / "baseline.json"
RESULTS_DIR = EVALS_DIR / "results"

Kind = Literal["gate", "ratchet", "info"]
Better = Literal["higher", "lower"]


@dataclass(frozen=True, slots=True)
class Spec:
    kind: Kind
    better: Better = "higher"
    limit: float | None = None  # gates: worst acceptable value
    tolerance: float = 1e-9  # ratchets: allowed drift before it counts
    label: str = ""


SPECS: dict[str, Spec] = {
    # Gates: these never move.
    "scenario_checks_failed": Spec("gate", "lower", 0, label="Scenario checks failed"),
    "invariant_violations": Spec("gate", "lower", 0, label="Invariant violations"),
    "soak_errors": Spec("gate", "lower", 0, label="Soak: missing creatives"),
    "server_decision_p95_ms": Spec(
        "gate", "lower", m.SERVER_DECISION_P95_MS, label="Server decision p95 (ms)"
    ),
    "loopback_round_trip_p95_ms": Spec(
        "gate", "lower", m.LOOPBACK_ROUND_TRIP_P95_MS, label="Agent→player round trip p95 (ms)"
    ),
    "glass_to_glass_p50_ms": Spec(
        "gate", "lower", m.GLASS_TO_GLASS_P50_MS, label="Glass-to-glass p50 (ms)"
    ),
    "glass_to_glass_p95_ms": Spec(
        "gate", "lower", m.GLASS_TO_GLASS_P95_MS, label="Glass-to-glass p95 (ms)"
    ),
    "minor_windows_missed": Spec("gate", "lower", 0, label="Perception: minor windows missed"),
    "inappropriate_ad_seconds": Spec(
        "gate", "lower", 0, label="Video: seconds of a must-never brand on screen"
    ),
    # Ratchets: deterministic product outcomes of the scripted suite.
    "scenario_pass_rate": Spec("ratchet", "higher", label="Scenario pass rate"),
    "fill_rate": Spec("ratchet", "higher", label="Fill rate"),
    "basket_rate": Spec("ratchet", "higher", label="Basket rate (≥2 brands / paid plays)"),
    "mean_coverage": Spec("ratchet", "higher", label="Mean audience coverage"),
    "basket_value_lift": Spec("ratchet", "higher", label="Basket value lift vs best solo"),
    "basket_revenue_lift": Spec(
        "ratchet", "higher", label="Revenue lift vs single-brand counterfactual"
    ),
    "decisions_swapped": Spec("info", label="Decisions that swapped the screen"),
    "decisions_kept_on_air": Spec("info", label="Decisions that kept the creative on air"),
    "flicker_per_minute": Spec("ratchet", "lower", tolerance=0.5, label="Soak: brand swaps/min"),
    # Info: tracked, shown, not judged against baseline.
    "brands_per_paid_play": Spec("info", label="Brands per paid play"),
    "decisions": Spec("info", label="Decisions observed"),
    "exclusions_competitor": Spec("info", label="Exclusions: competitor"),
    "exclusions_safety": Spec("info", label="Exclusions: safety"),
    "exclusions_below_reserve": Spec("info", label="Exclusions: below reserve"),
    "server_decision_p50_ms": Spec("info", "lower", label="Server decision p50 (ms)"),
    "server_decision_max_ms": Spec("info", "lower", label="Server decision max (ms)"),
    "loopback_round_trip_p50_ms": Spec("info", "lower", label="Round trip p50 (ms)"),
    "loopback_round_trip_max_ms": Spec("info", "lower", label="Round trip max (ms)"),
    "glass_to_glass_samples": Spec("info", label="Glass-to-glass samples"),
    "soak_decisions_per_s": Spec("info", label="Soak throughput (decisions/s)"),
    # Pipeline journeys (evals/pipeline.py): gen = live generation, demo = warmed caches.
    "house_ad_billed": Spec("gate", "lower", 0, label="Pipeline: house-ad plays billed"),
    "voice_duplicate_syntheses": Spec(
        "gate", "lower", 0, label="Pipeline: same line synthesized twice"
    ),
    "gen_hot_decision_p95_ms": Spec("gate", "lower", 30, label="Gen: hot decision p95 (ms)"),
    "gen_complete_ad_p95_ms": Spec(
        "gate", "lower", 3000, label="Gen: moment → complete ad p95 (ms)"
    ),
    "gen_billing_receipt_rate": Spec("gate", "higher", 1, label="Gen: paid plays with a receipt"),
    "gen_attention_error_mean": Spec(
        "gate", "lower", 0.05, label="Gen: attention-seconds error vs truth"
    ),
    "demo_complete_ad_p95_ms": Spec(
        "gate", "lower", 300, label="Demo: moment → complete ad p95 (ms, cache-hit target)"
    ),
    "demo_voice_provider_calls": Spec(
        "gate", "lower", 0, label="Demo: live TTS calls after warm-up"
    ),
    "demo_copy_provider_calls": Spec(
        "gate", "lower", 0, label="Demo: live LLM calls after warm-up"
    ),
    "video_clips": Spec("info", label="Video: clips"),
    "video_ad_precision": Spec("info", label="Video: ad precision (fitting / paid time)"),
    "video_sensible_ad_reach": Spec("info", label="Video: sensible-ad reach (fitting / clip time)"),
    "video_paid_share": Spec("info", label="Video: paid screen-time share"),
    "video_flicker_per_minute": Spec("info", "lower", label="Video: screen changes/min"),
    "video_clips_with_unmapped_signals": Spec(
        "info", "lower", label="Video: clips with objects seen but not understood"
    ),
}


@dataclass(frozen=True, slots=True)
class Verdict:
    metric: str
    value: float | None
    baseline: float | None
    status: Literal["pass", "fail", "improved", "regressed", "n/a", "info"]
    note: str = ""


def judge(metrics: dict[str, Any], baseline: dict[str, Any]) -> list[Verdict]:
    verdicts = []
    for name, value in metrics.items():
        spec = SPECS.get(name, Spec("info"))
        base = baseline.get(name)
        if value is None:
            note = (
                "needs player displayed_at (playback.report)" if "glass" in name else "no samples"
            )
            verdicts.append(Verdict(name, None, base, "n/a", note))
            continue
        if spec.kind == "gate":
            assert spec.limit is not None
            ok = value <= spec.limit if spec.better == "lower" else value >= spec.limit
            verdicts.append(
                Verdict(name, value, base, "pass" if ok else "fail", f"limit {spec.limit:g}")
            )
        elif spec.kind == "ratchet" and base is not None:
            delta = value - base if spec.better == "higher" else base - value
            if delta < -spec.tolerance:
                verdicts.append(Verdict(name, value, base, "regressed"))
            elif delta > spec.tolerance:
                verdicts.append(Verdict(name, value, base, "improved", "run make eval-baseline"))
            else:
                verdicts.append(Verdict(name, value, base, "pass"))
        else:
            verdicts.append(Verdict(name, value, base, "info"))
    return verdicts


def failed(verdicts: list[Verdict]) -> bool:
    return any(v.status in {"fail", "regressed"} for v in verdicts)


def load_baseline(path: Path = BASELINE_FILE) -> dict[str, Any]:
    if not path.exists():
        return {}
    return dict(json.loads(path.read_text(encoding="utf-8"))["metrics"])


def write_baseline(metrics: dict[str, Any], path: Path = BASELINE_FILE) -> None:
    ratchets = {k: v for k, v in metrics.items() if SPECS.get(k, Spec("info")).kind == "ratchet"}
    body = {"updated_at": _now(), "git_sha": git_info()["sha"], "metrics": ratchets}
    path.write_text(json.dumps(body, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def git_info() -> dict[str, Any]:
    def git(*args: str) -> str:
        try:
            return subprocess.run(
                ["git", *args], capture_output=True, text=True, check=True, cwd=EVALS_DIR
            ).stdout.strip()
        except OSError, subprocess.CalledProcessError:
            return ""

    return {
        "sha": git("rev-parse", "--short", "HEAD") or "unknown",
        "branch": git("rev-parse", "--abbrev-ref", "HEAD"),
        "dirty": bool(git("status", "--porcelain", "--untracked-files=no")),
    }


def save_run(report: dict[str, Any], markdown: str, out: Path = RESULTS_DIR) -> Path:
    """Write the run, refresh latest.*, append history.jsonl. Results are gitignored."""
    out.mkdir(parents=True, exist_ok=True)
    stamp = report["run_at"].replace(":", "").replace("-", "")[:15]
    path = out / f"{stamp}-{report['git']['sha']}.json"
    text = json.dumps(report, indent=2, default=str) + "\n"
    path.write_text(text, encoding="utf-8")
    (out / "latest.json").write_text(text, encoding="utf-8")
    (out / "latest.md").write_text(markdown, encoding="utf-8")
    summary = {
        "run_at": report["run_at"],
        "git": report["git"],
        "suite": report["suite"],
        "metrics": report["metrics"],
    }
    with (out / "history.jsonl").open("a", encoding="utf-8") as history:
        history.write(json.dumps(summary, default=str) + "\n")
    return path


def load_history(out: Path = RESULTS_DIR, *, suite: str, last: int = 6) -> list[dict[str, Any]]:
    path = out / "history.jsonl"
    if not path.exists():
        return []
    runs = [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    return [run for run in runs if run.get("suite") == suite][-last:]


def build_report(
    *,
    suite: str,
    metrics: dict[str, Any],
    verdicts: list[Verdict],
    sections: list[dict[str, Any]],
    violations: list[m.Violation],
    failures: list[str],
) -> dict[str, Any]:
    return {
        "run_at": _now(),
        "suite": suite,
        "git": git_info(),
        "passed": not failed(verdicts),
        "metrics": metrics,
        "verdicts": [asdict(v) for v in verdicts],
        "sections": sections,
        "violations": [asdict(v) for v in violations],
        "failures": failures,
    }


ICON = {"pass": "✅", "fail": "❌", "improved": "🟢", "regressed": "🔻", "n/a": "⚪", "info": "·"}
TREND_METRICS = (
    "scenario_pass_rate",
    "mean_coverage",
    "basket_revenue_lift",
    "server_decision_p95_ms",
    "loopback_round_trip_p95_ms",
)


def render_markdown(report: dict[str, Any], history: list[dict[str, Any]]) -> str:
    verdicts = [Verdict(**v) for v in report["verdicts"]]
    git = report["git"]
    lines = [
        f"# Eval scorecard: {report['suite']}",
        "",
        f"**{'PASS' if report['passed'] else 'FAIL'}** · `{git['sha']}`{' (dirty)' if git['dirty'] else ''}"
        f" on `{git['branch']}` · {report['run_at']}",
        "",
    ]
    order = {"gate": 0, "ratchet": 1, "info": 2}
    for kind, title in (
        ("gate", "Gates (absolute, never move)"),
        ("ratchet", "Ratchets (vs baseline)"),
        ("info", "Tracked"),
    ):
        rows = [v for v in verdicts if SPECS.get(v.metric, Spec("info")).kind == kind]
        if not rows:
            continue
        rows.sort(key=lambda v: (order[kind], v.metric))
        lines += [
            f"## {title}",
            "",
            "| | Metric | Value | Baseline | Note |",
            "|---|---|---:|---:|---|",
        ]
        for v in rows:
            label = SPECS.get(v.metric, Spec("info")).label or v.metric
            lines.append(
                f"| {ICON[v.status]} | {label} | {_fmt(v.value)} | {_fmt(v.baseline)} | {v.note} |"
            )
        lines.append("")
    if report["sections"]:
        lines += [
            "## Scenarios",
            "",
            "| | Scenario | Beat | Checks | Decisions | Time |",
            "|---|---|---|---:|---:|---:|",
        ]
        for s in report["sections"]:
            ok = s["checks_failed"] == 0 and not s.get("errors")
            lines.append(
                f"| {'✅' if ok else '❌'} | `{s['name']}` | {s.get('beat', '')} | "
                f"{s['checks'] - s['checks_failed']}/{s['checks']} | {s['decisions']} | {s['duration_s']:.2f}s |"
            )
        lines.append("")
    if report["failures"] or report["violations"]:
        lines += ["## Failures", ""]
        lines += [f"- {failure}" for failure in report["failures"]]
        lines += [
            f"- **{v['kind']}** in `{v['source']}` step {v['step']}: {v['detail']}"
            for v in report["violations"]
        ]
        lines.append("")
    if len(history) > 1:
        lines += [
            "## Trend (last runs of this suite)",
            "",
            "| Run | Sha | " + " | ".join(TREND_METRICS) + " |",
        ]
        lines.append("|---|---|" + "---:|" * len(TREND_METRICS))
        for run in history:
            values = " | ".join(_fmt(run["metrics"].get(name)) for name in TREND_METRICS)
            lines.append(f"| {run['run_at'][:16]} | `{run['git']['sha']}` | {values} |")
        lines.append("")
    return "\n".join(lines)


def _fmt(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.3f}" if abs(value) < 10 else f"{value:.1f}"
    return str(value)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
