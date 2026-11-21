import json

import pytest
from evals.speech_understanding import FIXTURE, run_case


@pytest.mark.parametrize("case", json.loads(FIXTURE.read_text()), ids=lambda c: c["id"])
def test_semantic_interpretation_controls_the_actual_player(case):
    result = run_case(case)
    assert result["passed"], result
