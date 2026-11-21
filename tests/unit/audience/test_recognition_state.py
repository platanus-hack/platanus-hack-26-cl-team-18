"""The replay state handles departures and delayed evidence without a model."""

from edge.agent.vision.state import AudienceState, Detection


def person(track_id: int, x: float = 0) -> Detection:
    return Detection("person", 0.9, (x, 0, x + 100, 200), track_id)


def test_departure_and_late_evidence_remain_in_history():
    state = AudienceState("test", exit_after=1)
    state.observe(0.5, [person(1)])
    state.observe(1.0, [person(1, 20)])
    state.observe(2.5, [person(2)])
    state.enrich(1, "upper_clothing", "black hoodie", 1.0, "vlm", "visible garment", 0.8)
    snapshot = state.snapshot()
    assert snapshot["people_count"] == 1
    assert snapshot["people"][0]["track_id"] == 2
    assert snapshot["history"][0]["attributes"]["upper_clothing"]["value"] == "black hoodie"
    assert snapshot["history"][0]["direction"] == "right"


def test_stronger_evidence_and_out_of_order_frames():
    state = AudienceState("test")
    state.observe(1.0, [person(1), Detection("bicycle", 0.8, (0, 0, 4, 4))])
    state.enrich(1, "brand", "Nike", 1.0, "vlm", "visible logo", 0.9)
    state.enrich(1, "brand", "unknown", 1.0, "vlm", "unclear", 0.2)
    state.observe(2.0, [])
    state.observe(1.5, [person(1)])
    snapshot = state.snapshot()
    assert snapshot["timestamp"] == 2.0
    assert not snapshot["people"][0]["currently_visible"]
    assert snapshot["people"][0]["attributes"]["brand"]["value"] == "Nike"
    assert snapshot["scene"]["recently_observed"]["bicycle"]["count"] == 1
    state.observe(3.1, [])
    assert not state.snapshot()["scene"]["recently_observed"]


def test_rejects_evidence_from_outside_track_lifetime():
    state = AudienceState("test")
    state.observe(1.0, [person(1)])
    state.enrich(1, "brand", "Nike", 0.5, "vlm", "logo", 1)
    state.enrich(2, "brand", "Nike", 1.0, "vlm", "logo", 1)
    assert state.snapshot()["people"][0]["attributes"] == {}


def test_finish_removes_active_people_even_without_exit_timeout():
    state = AudienceState("test")
    state.observe(0.9, [person(1)])
    state.finish(1.0)
    snapshot = state.snapshot()
    assert snapshot["finished"]
    assert snapshot["people_count"] == 0
    assert snapshot["history"][0]["track_id"] == 1
