from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from agent.speech.echo_guard import AudioWindow, EchoGuard
from agent.speech.vad import FRAME_BYTES, FRAME_SAMPLES, EnergyVad, GateEvent, SpeechGate

T0 = datetime(2026, 11, 21, 15, tzinfo=UTC)


def tone(amplitude: float = 0.3) -> bytes:
    t = np.arange(FRAME_SAMPLES) / 16_000
    return (np.sin(2 * np.pi * 220 * t) * amplitude * 32767).astype(np.int16).tobytes()


SILENCE = bytes(FRAME_BYTES)


def run(gate: SpeechGate, frames: list[bytes]) -> list[tuple[GateEvent, int]]:
    out: list[tuple[GateEvent, int]] = []
    for frame in frames:
        out.extend((o.event, len(o.audio)) for o in gate.feed(frame))
    return out


def test_energy_vad_separates_silence_from_voice() -> None:
    vad = EnergyVad()
    assert vad.speech_probability(SILENCE) == 0.0
    assert vad.speech_probability(tone()) == 1.0


def test_gate_opens_with_preroll_and_closes_after_hangover() -> None:
    gate = SpeechGate(EnergyVad(), start_frames=3, hangover_frames=4, preroll_frames=5)
    events = run(gate, [SILENCE] * 4 + [tone()] * 3 + [tone()] * 2 + [SILENCE] * 4 + [SILENCE] * 3)
    kinds = [e for e, _ in events]
    assert kinds[0] is GateEvent.SPEECH_START
    # Pre-roll: 2 silent + 3 voiced frames that triggered the opening.
    assert events[1] == (GateEvent.AUDIO, 5 * FRAME_BYTES)
    assert kinds.count(GateEvent.SPEECH_END) == 1
    assert not gate.is_open
    # Frames after the close are not streamed.
    assert kinds[-1] is GateEvent.SPEECH_END


def test_short_noise_burst_does_not_open() -> None:
    gate = SpeechGate(EnergyVad(), start_frames=3)
    assert run(gate, [tone(), tone(), SILENCE, tone(), SILENCE]) == []


def test_segment_is_capped() -> None:
    gate = SpeechGate(EnergyVad(), start_frames=1, preroll_frames=1, max_segment_frames=5)
    kinds = [e for e, _ in run(gate, [tone()] * 10)]
    assert kinds.count(GateEvent.SPEECH_END) >= 1


def test_gate_rejects_wrong_frame_size() -> None:
    with pytest.raises(ValueError):
        list(SpeechGate(EnergyVad()).feed(b"\0" * 10))


def test_echo_guard_drops_speech_inside_a_playback_window() -> None:
    guard = EchoGuard()
    guard.announce(
        AudioWindow(T0, T0 + timedelta(seconds=3), "¿Hambre post-gym? Voltio y Café Patio.")
    )
    assert guard.is_echo("cualquier cosa", T0 + timedelta(seconds=1))
    assert guard.is_echo("cualquier cosa", T0 + timedelta(seconds=3, milliseconds=300))  # tail
    assert not guard.is_echo("cualquier cosa", T0 + timedelta(seconds=5))


def test_echo_guard_drops_our_own_line_heard_late() -> None:
    guard = EchoGuard()
    guard.announce(
        AudioWindow(T0, T0 + timedelta(seconds=3), "¿Hambre post-gym? Voltio y Café Patio.")
    )
    later = T0 + timedelta(seconds=10)
    assert guard.is_echo("hambre post gym voltio y cafe patio", later)
    assert guard.is_echo("voltio y café patio", later)  # a fragment
    assert not guard.is_echo("uf qué hambre", later)  # a person, not our ad
    assert guard.dropped == 2


def test_echo_guard_rejects_inverted_windows() -> None:
    with pytest.raises(ValueError):
        EchoGuard().announce(AudioWindow(T0, T0 - timedelta(seconds=1), "x"))


def test_end_segment_closes_without_replaying_streamed_audio() -> None:
    gate = SpeechGate(EnergyVad(), start_frames=2, preroll_frames=4)
    run(gate, [tone()] * 6)
    assert gate.is_open and gate.end_segment() and not gate.is_open
    assert not gate.end_segment()  # idempotent
    events = run(gate, [tone()] * 2)
    assert events[0][0] is GateEvent.SPEECH_START
    assert events[1] == (GateEvent.AUDIO, 2 * FRAME_BYTES)  # fresh pre-roll only
