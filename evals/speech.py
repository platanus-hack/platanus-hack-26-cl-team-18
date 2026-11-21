"""Speech regression suite; separate from the fixed economic benchmark's traffic mix."""

from evals.harness import RunRecord, run_scenario
from evals.scenario import SCENARIO_DIR, load_scenarios


def run_speech_scenarios() -> list[RunRecord]:
    return [run_scenario(scenario) for scenario in load_scenarios(SCENARIO_DIR / "speech")]
