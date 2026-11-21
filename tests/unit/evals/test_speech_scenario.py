import pytest
from evals.harness import run_scenario
from evals.metrics import invariant_violations
from evals.scenario import SCENARIO_DIR, load_scenarios


@pytest.mark.parametrize("name", ["speech-coffee", "speech-refreshes-copy"])
def test_coffee_request_reaches_the_matching_player_and_survives_camera_updates(name):
    (scenario,) = load_scenarios(SCENARIO_DIR / "speech", only={name})
    assert scenario.steps[1].speech
    run = run_scenario(scenario)
    assert [check for check in run.checks if not check.passed] == []
    assert invariant_violations(run.decisions) == []
