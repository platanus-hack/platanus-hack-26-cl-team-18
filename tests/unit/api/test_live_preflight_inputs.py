from scripts.live_preflight import live_input_failures


def test_wrong_backend_fails_even_when_its_health_is_ok():
    failures = live_input_failures(
        {"status": "ok", "screens_connected": []}, "demo-01", require_speech=True
    )
    assert len(failures) == 3


def test_camera_without_the_matching_player_is_not_a_ready_demo():
    report = {"inputs": {"demo-01": {"camera_recent": True}}, "screens_connected": ["other"]}
    assert live_input_failures(report, "demo-01") == [
        "demo-01: no player connected to this backend"
    ]


def test_quiet_speech_is_not_reported_as_recognized():
    report = {
        "inputs": {"demo-01": {"camera_recent": True, "speech_enabled": True}},
        "screens_connected": ["demo-01"],
    }
    assert "no speech event" in live_input_failures(report, "demo-01", require_speech=True)[0]
    report["inputs"]["demo-01"]["speech_recent"] = True
    assert live_input_failures(report, "demo-01", require_speech=True) == []
