from evals.scorecard import failed, judge


def test_gates_are_absolute_and_ratchets_follow_direction() -> None:
    verdicts = {
        v.metric: v.status
        for v in judge(
            {
                "invariant_violations": 1,
                "server_decision_p95_ms": 12.0,
                "mean_coverage": 0.9,
                "fill_rate": 0.8,
                "glass_to_glass_p95_ms": None,
            },
            {"mean_coverage": 1.0, "fill_rate": 0.7},
        )
    }
    assert verdicts == {
        "invariant_violations": "fail",
        "server_decision_p95_ms": "pass",
        "mean_coverage": "regressed",
        "fill_rate": "improved",
        "glass_to_glass_p95_ms": "n/a",
    }


def test_improvement_alone_does_not_fail_the_run() -> None:
    assert not failed(judge({"fill_rate": 0.9}, {"fill_rate": 0.7}))
    assert failed(judge({"fill_rate": 0.6}, {"fill_rate": 0.7}))
