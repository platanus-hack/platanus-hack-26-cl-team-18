import pytest
from evals.harness import run_scenario
from evals.metrics import invariant_violations
from evals.scenario import SCENARIO_DIR, load_scenarios


@pytest.mark.parametrize(
    "name",
    [
        "speech-coffee",
        "speech-refreshes-copy",
        "speech-coffee-beer-switch",
        "speech-beer-safety",
        "speech-corrections",
        "speech-same-utterance-correction",
    ],
)
def test_coffee_request_reaches_the_matching_player_and_survives_camera_updates(name):
    (scenario,) = load_scenarios(SCENARIO_DIR / "speech", only={name})
    assert scenario.steps[1].speech
    run = run_scenario(scenario)
    assert [check for check in run.checks if not check.passed] == []
    assert invariant_violations(run.decisions) == []


def test_hall_chatter_that_only_sounds_like_a_need_never_changes_the_ad():
    (scenario,) = load_scenarios(SCENARIO_DIR / "speech", only={"speech-venue-chatter"})
    run = run_scenario(scenario)
    assert [check for check in run.checks if not check.passed] == []
    assert invariant_violations(run.decisions) == []


def test_repeated_requests_also_switch_with_real_minimum_ad_times_enabled():
    (scenario,) = load_scenarios(SCENARIO_DIR / "speech", only={"speech-coffee-beer-switch"})
    run = run_scenario(scenario, swap_holds=True)
    assert [check for check in run.checks if not check.passed] == []
    assert invariant_violations(run.decisions) == []
    assert any(d.swap == "held" for d in run.decisions)
