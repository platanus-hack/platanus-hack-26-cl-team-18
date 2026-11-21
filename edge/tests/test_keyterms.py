from __future__ import annotations

import pytest

from agent.speech.keyterms import DEFAULT_KEYTERMS, KeytermCatalog, PartialHitTracker, normalize


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("¡Qué HAMBRE!", "que hambre"),
        ("bajón", "bajon"),
        ("Año  nuevo...", "año nuevo"),
        ("café-con-leche", "cafe con leche"),
    ],
)
def test_normalize(raw: str, expected: str) -> None:
    assert normalize(raw) == expected


def test_matches_variants_accent_and_case_insensitively() -> None:
    assert DEFAULT_KEYTERMS.match("uf, QUÉ BAJON tengo") == ("hambre",)
    assert DEFAULT_KEYTERMS.match("vamos al gimnasio y después un cortado") == ("café", "gym")


@pytest.mark.parametrize(
    "phrase",
    ["quiero una cerveza", "quiero cervezas", "una cervecita", "unas cervecitas", "una chela"],
)
def test_beer_requests_have_the_same_canonical_intent(phrase: str) -> None:
    assert DEFAULT_KEYTERMS.match(phrase) == ("cerveza",)


def test_respects_word_boundaries() -> None:
    catalog = KeytermCatalog.from_mapping({"gym": ()})
    assert catalog.match("la gymkana del colegio") == ()
    assert catalog.match("al gym") == ("gym",)


def test_multiword_variants() -> None:
    catalog = KeytermCatalog.from_mapping({"hambre": ("tengo hambre",), "sed": ("tengo sed",)})
    assert catalog.match("oye tengo sed") == ("sed",)


def test_catalog_rejects_ambiguous_variants() -> None:
    with pytest.raises(ValueError, match="maps to both"):
        KeytermCatalog.from_mapping({"a": ("chela",), "b": ("Chela",)})


def test_partial_tracker_reports_each_hit_once_then_resets_on_commit() -> None:
    tracker = PartialHitTracker(DEFAULT_KEYTERMS)
    assert tracker.on_partial("uf qué") == ()
    assert tracker.on_partial("uf qué hambre") == ("hambre",)
    assert tracker.on_partial("uf qué hambre, vamos") == ()  # already reported
    assert tracker.on_partial("uf qué hambre, vamos por un café") == ("café",)
    assert tracker.on_committed("Uf, qué hambre. Vamos por un café.") == ("hambre", "café")
    assert tracker.on_partial("hambre otra vez") == ("hambre",)  # new segment


def test_prompt_terms_are_bounded_and_unique() -> None:
    terms = DEFAULT_KEYTERMS.prompt_terms(limit=10)
    assert len(terms) == 10
    assert len(set(terms)) == 10


def test_prompt_terms_cover_every_canonical_first_and_respect_scribe_limits() -> None:
    terms = DEFAULT_KEYTERMS.prompt_terms()
    assert terms[: len(DEFAULT_KEYTERMS.variants)] == tuple(DEFAULT_KEYTERMS.variants)
    assert len(terms) <= 50
    assert sum(map(len, terms)) <= 1000
    assert DEFAULT_KEYTERMS.prompt_terms(max_chars=12) == ("hambre", "comida")
