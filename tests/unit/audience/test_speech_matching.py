"""Conservative, shared lexical understanding of synthetic shopping requests."""

import pytest

from app.contract.speech_matching import DEFAULT_KEYTERMS


@pytest.mark.parametrize(
    ("text", "wanted", "rejected"),
    [
        ("no quiero cerveza, quiero café", ("café",), ("cerveza",)),
        ("ya no quiero café", (), ("café",)),
        # The beer is rejected; asking for it without alcohol is its own want.
        ("quiero cerveza sin alcohol", ("sin alcohol",), ("cerveza",)),
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
        ("Cerveza sin alcohol y café", ("café", "sin alcohol"), ("cerveza",)),
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


# Noisy venue (third speech audit): a hackathon hall talks about models, the
# time and prices all day, and our own voice-overs speak prices ("once mil").
# Each case was a false instant topic, or a missed one, before the fix.
@pytest.mark.parametrize(
    ("text", "wanted"),
    [
        # Our own offer line, echoed back: "once" (eleven) is not the afternoon tea.
        ("Monster y Pronto Copec para tres: once mil novecientos noventa pesos", ()),
        ("son las once", ()),
        ("nos vemos a las once y media", ()),
        ("éramos once personas", ()),
        ("tomemos once", ("comida",)),
        ("hora de la once", ("comida",)),
        # Hackathon chatter.
        ("hay que entrenar el modelo de nuevo", ()),
        ("voy a entrenar", ("gym",)),
        ("qué bajón, perdimos", ()),
        ("tengo bajón", ("hambre",)),
        ("uf, qué bajón tengo", ("hambre",)),
        ("a las once me tomo un café", ("café",)),
        ("el equipo está completo", ()),
        ("quiero un completo", ("comida",)),
        ("mi sueño es ganar", ()),
        ("tengo sueño", ("energía",)),
        # Negation by quantifier, or a need that already passed.
        ("nadie tiene hambre", ()),
        ("nunca tomo café", ()),
        ("se me quitó el hambre", ()),
        # Common recognizer slips under noise (matched, never prompted).
        ("uf que ambre", ("hambre",)),
        ("tengo hambe", ("hambre",)),
        # Code-switching.
        ("I'm so hungry", ("hambre",)),
        ("I need a coffee", ("café",)),
        ("I don't want coffee", ()),
    ],
)
def test_venue_chatter_and_noisy_transcripts(text, wanted):
    assert DEFAULT_KEYTERMS.understand(text).wanted == wanted


def test_misheard_forms_are_matched_but_never_bias_the_recognizer():
    prompted = {term.lower() for term in DEFAULT_KEYTERMS.prompt_terms()}
    assert DEFAULT_KEYTERMS.understand("quiero un acfe").wanted == ("café",)
    assert not prompted & {"acfe", "ambre", "hambe", "hungry", "coffee"}


@pytest.mark.parametrize(
    ("text", "wanted"),
    [
        ("quiero un helado", ("helado",)),
        ("unas papas fritas", ("papas",)),
        ("las papas", ("papas",)),
        ("un chocolatito", ("chocolate",)),
        ("algo dulce", ("dulce",)),
        ("un juguito de naranja", ("jugo",)),
        ("una chocolatada bien fría", ("leche",)),
        ("una bebida", ("bebida",)),
        ("algo isotónico", ("isotónica",)),
        ("I want ice cream", ("helado",)),
        # The other meaning, in Chilean Spanish.
        ("está helado afuera", ()),
        ("qué helado hace", ()),
        ("mis papás vienen a buscarme", ()),
        ("los papás del equipo", ()),
        ("qué dulce eres", ()),
        ("es muy dulce", ()),
        ("no quiero helado", ()),
    ],
)
def test_chilean_store_words_and_their_other_meanings(text, wanted):
    assert DEFAULT_KEYTERMS.understand(text).wanted == wanted


def test_the_recognizer_prompt_still_covers_every_intent_within_scribe_limits():
    terms = DEFAULT_KEYTERMS.prompt_terms()
    assert set(DEFAULT_KEYTERMS.variants) <= set(terms)
    assert len(terms) <= 50 and sum(map(len, terms)) <= 1000
