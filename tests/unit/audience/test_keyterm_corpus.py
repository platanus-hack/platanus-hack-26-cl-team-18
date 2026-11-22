"""The spoken-phrase corpus: a feedback loop for the hot-path keyterm matcher.

evals/fixtures/speech/keyterms_es_cl.json lists es-CL phrases and the intents they must
(and must not) produce. A rehearsal slip becomes one line there: red until the matcher
gets the variant or the guard. Coverage rules keep the corpus growing with the matcher:
every intent, every aisle the Chile screen answers by voice, and every word with a
second meaning has a phrase that proves it.
"""

import json
from pathlib import Path

import pytest

from app.bootstrap.demo_catalog_chile import CHILE
from app.contract.speech_matching import _OTHER_SENSE, DEFAULT_KEYTERMS, normalize

CORPUS = Path(__file__).resolve().parents[3] / "evals/fixtures/speech/keyterms_es_cl.json"
CASES = json.loads(CORPUS.read_text(encoding="utf-8"))["cases"]


@pytest.mark.parametrize("case", CASES, ids=[case["text"] for case in CASES])
def test_each_phrase_reads_as_its_intents(case: dict) -> None:
    meaning = DEFAULT_KEYTERMS.understand(case["text"])
    assert list(meaning.wanted) == case["wanted"], case.get("why", "")
    assert list(meaning.rejected) == case["rejected"], case.get("why", "")


def test_every_intent_has_a_phrase_that_triggers_it() -> None:
    triggered = {intent for case in CASES for intent in case["wanted"]}
    assert set(DEFAULT_KEYTERMS.variants) <= triggered


def test_every_aisle_the_chile_screen_answers_by_voice_has_a_phrase() -> None:
    spoken = {label for label, _ in CHILE.cue_segments if label in DEFAULT_KEYTERMS.variants}
    triggered = {intent for case in CASES for intent in case["wanted"]}
    assert spoken <= triggered


def test_every_word_with_a_second_meaning_has_a_phrase_proving_it_is_ignored() -> None:
    quiet = [normalize(case["text"]).split() for case in CASES if not case["wanted"]]
    for surface in _OTHER_SENSE:
        assert any(surface in words for words in quiet), surface
