"""Conservative, shared lexical understanding of synthetic shopping requests."""

import pytest

from app.contract.speech_matching import DEFAULT_KEYTERMS


@pytest.mark.parametrize(
    ("text", "wanted", "rejected"),
    [
        ("no quiero cerveza, quiero café", ("café",), ("cerveza",)),
        ("ya no quiero café", (), ("café",)),
        ("quiero cerveza sin alcohol", (), ("cerveza",)),
        ("quiero café y cerveza", ("café", "cerveza"), ()),
        ("no café ni cerveza", (), ("café", "cerveza")),
        ("no quiero café pero sí una chela", ("cerveza",), ("café",)),
        ("quiero un acfe", ("café",), ()),
        ("quiero agua", ("sed",), ()),
        ("cafetería gymkana", (), ()),
    ],
)
def test_request_semantics(text, wanted, rejected):
    meaning = DEFAULT_KEYTERMS.understand(text)
    assert meaning.wanted == wanted
    assert meaning.rejected == rejected


@pytest.mark.parametrize("text", ["cerveza 0.0", "cerveza 0,0", "cerveza no alcohólica"])
def test_zero_alcohol_requests_never_activate_the_alcohol_category(text):
    meaning = DEFAULT_KEYTERMS.understand(text)
    assert "cerveza" not in meaning.wanted
    assert "cerveza" in meaning.rejected


@pytest.mark.parametrize(
    ("text", "wanted", "rejected"),
    [
        ("Una chela no, mejor café", ("café",), ("cerveza",)),
        ("Café en vez de cerveza", ("café",), ("cerveza",)),
        ("No solo café, también cerveza", ("café", "cerveza"), ()),
        ("Cerveza sin alcohol y café", ("café",), ("cerveza",)),
        ("El anuncio dice cerveza", (), ()),
        ("Qué significa la palabra café", (), ()),
        ("Si quisiera cerveza", (), ()),
        ("El anuncio dice cerveza pero yo quiero café", ("café",), ()),
    ],
)
def test_corrections_and_background_mentions_do_not_create_false_wants(text, wanted, rejected):
    meaning = DEFAULT_KEYTERMS.understand(text)
    assert meaning.wanted == wanted
    assert meaning.rejected == rejected


@pytest.mark.parametrize(
    ("text", "wanted", "rejected"),
    [
        ("quiero cerveza mejor un café", ("café",), ("cerveza",)),
        ("quiero un café. Quiero una cerveza. Mejor un café", ("café",), ("cerveza",)),
        ("cerveza, no, prefiero café", ("café",), ("cerveza",)),
        ("cerveza, más bien café", ("café",), ("cerveza",)),
        ("café y cerveza", ("café", "cerveza"), ()),
        ("cerveza y el mejor café", ("café", "cerveza"), ()),
    ],
)
def test_explicit_correction_replaces_earlier_wants_in_one_stt_segment(text, wanted, rejected):
    meaning = DEFAULT_KEYTERMS.understand(text)
    assert meaning.wanted == wanted
    assert meaning.rejected == rejected
