"""The offline creative eval is part of `make check`: every gate must pass (docs/creative-generation.md §4)."""

from __future__ import annotations

import anyio
import pytest
from evals.creative import runner

WALL_CLOCK = frozenset({"hot_compose_p95_ms"})


@pytest.mark.parametrize("writer", ["stock", "recorded"])
def test_offline_creative_eval_passes_every_gate(writer: runner.WriterKind) -> None:
    report = anyio.run(lambda: runner.run(writer))
    rows = runner.judge(report.metrics, runner.load_baseline(writer))
    # Wall-clock gates depend on machine load; `make eval-creative` gates them, not unit tests.
    gates = {r["metric"]: r for r in rows if r["kind"] == "gate" and r["metric"] not in WALL_CLOCK}
    failing = {k: r["value"] for k, r in gates.items() if r["status"] == "FAIL"}
    assert not failing, runner.render(report, rows)
    worse = [r["metric"] for r in rows if r["status"] == "WORSE"]
    assert not worse, f"ratchets regressed vs evals/creative/baseline.json: {worse}"


def test_recorded_mode_exercises_every_fallback_path() -> None:
    """The fixtures must keep covering accept, reject, lower score and provider failure."""
    report = anyio.run(lambda: runner.run("recorded"))
    assert {"accepted", "rejected", "lower_score", "failed"} <= set(report.outcomes)
    assert report.metrics["aired_guard_violations"] == 0
    assert (report.metrics["ai_air_rate"] or 0) > 0.5


def test_adversarial_suite_is_fully_rejected() -> None:
    results = runner.run_adversarial(runner.load_suite())
    assert results and all(r["rejected"] for r in results), [
        r for r in results if not r["rejected"]
    ]
