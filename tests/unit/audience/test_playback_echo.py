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


# Noisy-venue echo (third speech audit): the speakerphone's AEC leaks some of our
# own voice-over, and Scribe's *partials* of it carry one recognition error.
# Measured on the Chile catalog's keyterm-bearing spoken lines, 20 % of
# single-error partials leaked a keyterm, so the ad re-targeted itself.
NESCAFE = "Nescafé. Un café rápido antes de seguir."
OFFER_ONCE = "Monster y Pronto Copec para tres: once mil novecientos noventa pesos"


def test_a_misrecognized_partial_of_our_own_line_is_still_our_line():
    guard = PlaybackEcho()
    guard.start(NESCAFE, T0, timedelta(seconds=4))
    at = T0 + timedelta(seconds=1)
    assert guard.matches("nescafe un cafes rapido", at)  # one inflection error
    assert guard.matches("nescafe un un cafe", at)  # a repeated word
    assert guard.matches("nes cafe un cafe rapido", at)  # a split brand name
    guard.start("Benedictino. Agua fría para el camino.", T0, timedelta(seconds=4))
    assert guard.matches("benedictino benedictino agua", at)  # a stuttering partial
    guard.start(OFFER_ONCE, T0, timedelta(seconds=4))
    assert guard.matches("para tre once mil novecientos", at)


def test_a_real_request_over_our_voice_over_is_never_swallowed():
    guard = PlaybackEcho()
    guard.start(NESCAFE, T0, timedelta(seconds=4))
    at = T0 + timedelta(seconds=1)
    for said in (
        "quiero un café",
        "un café por favor",
        "me tomaría un café rápido",
        "uf qué hambre",
        "tengo sed",
    ):
        assert not guard.matches(said, at), said
