from datetime import UTC, datetime, timedelta

from app.modules.audience.application.playback_echo import PlaybackEcho

T0 = datetime(2026, 11, 21, tzinfo=UTC)


def test_only_actual_ad_fragments_are_rejected_and_the_window_expires():
    guard = PlaybackEcho()
    line = "Disfruta tu café con una pausa deliciosa"
    assert not guard.matches(line, T0)
    guard.start(line, T0, timedelta(seconds=5))
    assert guard.matches("tu café con una pausa deliciosa", T0)
    assert not guard.matches("quiero cerveza", T0)
    assert not guard.matches("café", T0)
    assert not guard.matches(line, T0 + timedelta(seconds=8))


def test_interrupted_audio_only_keeps_a_short_echo_tail():
    guard = PlaybackEcho()
    guard.start("Una cerveza para compartir", T0, timedelta(seconds=30))
    guard.stop(T0 + timedelta(seconds=1))
    assert guard.matches("cerveza para compartir", T0 + timedelta(seconds=1.2))
    assert not guard.matches("cerveza para compartir", T0 + timedelta(seconds=4))


def test_late_transcript_of_the_previous_voice_is_filtered_after_a_swap():
    guard = PlaybackEcho()
    guard.start("Una cerveza para compartir", T0, timedelta(seconds=2))
    guard.start("Tu café con una pausa deliciosa", T0 + timedelta(seconds=2), timedelta(seconds=3))
    assert guard.matches("cerveza para compartir", T0 + timedelta(seconds=3))
    assert not guard.matches("quiero una cerveza", T0 + timedelta(seconds=3))
    assert not guard.matches("cerveza para compartir", T0 + timedelta(seconds=5))
