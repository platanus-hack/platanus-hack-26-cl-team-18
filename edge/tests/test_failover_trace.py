"""The latency trace must account for failed attempts and identify the result's source."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from agent.vision.__main__ import argument_parser
from agent.vision.detector import CompositeDetector, DetectorUnavailable, FailoverConfig
from agent.vision.remote import RemoteGpuDetector
from tests.test_vision import T0, RecordingLink, _pipeline, failover, frame, marker_at


def test_slow_recovery_probe_is_included_in_fallback_latency():
    detector, remote, _, clock = failover()
    remote.down = True
    detector.detect(None, T0)
    clock.now += 11
    remote.down = False
    remote.latency_ms = 400
    result = detector.detect(None, T0)
    assert result.detector == "local"
    assert result.inference_ms == pytest.approx(430)
    assert result.extras["primary_attempt_ms"] == pytest.approx(400)


def test_timed_out_primary_is_included_in_fallback_latency():
    detector, remote, local, clock = failover()

    def timeout(_, at):
        clock.now += 0.15
        raise DetectorUnavailable("timeout")

    remote.detect = timeout
    result = detector.detect(None, T0)
    assert result.inference_ms == pytest.approx(180)
    assert result.extras["primary_attempt_ms"] == pytest.approx(150)
    assert local.calls == 1


def test_markers_report_the_actual_source_on_the_frame_that_triggers_failover():
    detector, _, _, _ = failover(latency_ms=200)
    markers = SimpleNamespace(
        detect=lambda _, at: replace(
            detector._secondary.detect(None, at), markers=(marker_at(1, 500),)
        )
    )
    combined = CompositeDetector(detector, markers)
    for _ in range(5):
        result = combined.detect(None, T0)
    assert detector.active == "local"
    assert result.detector == "remote"


@pytest.mark.parametrize("limit", [0, -1, 3, 10])
def test_remote_in_flight_limit_cannot_exceed_two(limit):
    with pytest.raises(ValueError, match="max_in_flight"):
        RemoteGpuDetector("ws://localhost", max_in_flight=limit, encoder=lambda _: (b"", 1, 1))


@pytest.mark.parametrize(
    "kwargs",
    [
        {"budget_ms": 0},
        {"budget_ms": float("nan")},
        {"window": 0},
        {"window": 2, "min_samples": 3},
        {"recovery_s": -1},
    ],
)
def test_failover_rejects_configuration_that_disables_health_checks(kwargs):
    with pytest.raises(ValueError):
        FailoverConfig(**kwargs)


def test_live_gpu_deadlines_match_the_budget_and_are_configurable():
    parser = argument_parser()
    args = parser.parse_args(["--screen", "demo-01"])
    assert args.gpu_timeout_ms == 150
    assert args.gpu_connect_timeout_ms == 500
    args = parser.parse_args(
        ["--screen", "demo-01", "--gpu-timeout-ms", "100", "--gpu-connect-timeout-ms", "300"]
    )
    assert args.gpu_timeout_ms == 100
    assert args.gpu_connect_timeout_ms == 300


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf"])
def test_live_gpu_deadlines_reject_invalid_values(value):
    with pytest.raises(SystemExit):
        argument_parser().parse_args(["--screen", "demo-01", "--gpu-timeout-ms", value])


def test_pipeline_publishes_only_the_approved_perception_timing_fields():
    pipeline = _pipeline(RecordingLink())
    detections = replace(
        frame(T0),
        detector="local",
        inference_ms=430,
        extras={"primary_attempt_ms": 400, "unrelated_adapter_detail": 123},
    )
    snapshot = pipeline.process(None, detections)
    assert snapshot.perception.model_dump() == {
        "detector": "local",
        "inference_ms": 430,
        "round_trip_ms": None,
        "primary_attempt_ms": 400,
    }
