"""The eval harness against a real backend: guards the harness and the demo beats."""

from evals.harness import run_scenario
from evals.metrics import invariant_violations
from evals.scenario import load_scenarios


def test_every_scenario_file_parses() -> None:
    names = [scenario.name for scenario in load_scenarios()]
    assert len(names) == len(set(names)) and "basket-ad" in names


def test_demo_run_scenario_passes_on_the_live_loop() -> None:
    (scenario,) = load_scenarios(only={"demo-run"})
    run = run_scenario(scenario)
    assert [c for c in run.checks if not c.passed] == []
    assert invariant_violations(run.decisions) == []
