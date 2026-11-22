"""Raw /screen partials are stabilized server-side; alcohol is final-only for every source."""

from app.modules.audience.application.partial_gate import PartialGate


def raw(gate: PartialGate, wanted: tuple[str, ...], *, uid: str = "w0", rejected=()) -> bool:
    return gate.admit(
        utterance_id=uid, is_final=False, stabilized=False, wanted=wanted, rejected=rejected
    )


def test_a_one_partial_invention_never_acts() -> None:
    gate = PartialGate()
    assert not raw(gate, ("sopaipilla",))  # babble flicker
    assert not raw(gate, ())  # gone on the next partial
    assert not raw(gate, ())


def test_a_real_word_acts_on_the_second_agreeing_partial_and_only_once() -> None:
    gate = PartialGate()
    assert not raw(gate, ("cafe",))
    assert raw(gate, ("cafe",))
    assert not raw(gate, ("cafe",))  # Scribe re-sends unchanged partials: no re-decide
    assert not raw(gate, ("cafe", "dulce"))
    assert raw(gate, ("cafe", "dulce"))


def test_a_stable_retraction_acts() -> None:
    gate = PartialGate()
    raw(gate, ("cafe",))
    raw(gate, ("cafe",))
    assert not raw(gate, (), rejected=("cafe",))
    assert raw(gate, (), rejected=("cafe",))


def test_a_new_utterance_starts_counting_again() -> None:
    gate = PartialGate()
    raw(gate, ("cafe",))
    assert not raw(gate, ("cafe",), uid="w1")
    assert raw(gate, ("cafe",), uid="w1")


def test_alcohol_waits_for_the_final_on_raw_partials() -> None:
    gate = PartialGate()
    for _ in range(4):
        assert not raw(gate, ("cerveza",))
    assert gate.admit(
        utterance_id="w0", is_final=True, stabilized=False, wanted=("cerveza",), rejected=()
    )


def test_rejecting_alcohol_early_is_allowed() -> None:
    gate = PartialGate()
    raw(gate, (), rejected=("cerveza",))
    assert raw(gate, (), rejected=("cerveza",))


def test_edge_partials_are_already_stable_and_pass_through() -> None:
    gate = PartialGate()
    assert gate.admit(
        utterance_id="e:0", is_final=False, stabilized=True, wanted=("cafe",), rejected=()
    )


def test_finals_always_pass_and_reset() -> None:
    gate = PartialGate()
    raw(gate, ("cafe",))
    raw(gate, ("cafe",))
    assert gate.admit(utterance_id="w0", is_final=True, stabilized=False, wanted=(), rejected=())
    assert not raw(gate, ("cafe",))  # same id after a final: counting restarts
