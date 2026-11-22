"""Transcription noise must not change what the hot-path matcher hears.

In a loud hall Scribe's partials come back shouted, without accents, wrapped in
punctuation, with a filler or a stutter, or with half a word from the next table.
For every phrase in the es-CL corpus (evals/fixtures/speech/keyterms_es_cl.json),
each noise below must leave its wanted/rejected intents exactly as they were:
a demo that fires on "tengo hambre" but not on "¿TENGO HAMBRE?" fails on stage.
"""

from __future__ import annotations

import json
import unicodedata
from collections.abc import Callable
from pathlib import Path

import pytest

from app.contract.speech_matching import DEFAULT_KEYTERMS

CORPUS = Path(__file__).resolve().parents[3] / "evals/fixtures/speech/keyterms_es_cl.json"
CASES = json.loads(CORPUS.read_text(encoding="utf-8"))["cases"]


def _strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text)
    # Keep ñ: Scribe writes it, and "ano"/"año" are different words.
    return unicodedata.normalize(
        "NFC", "".join(c for c in decomposed if unicodedata.category(c) != "Mn" or c == "̃")
    )


def _stutter(text: str) -> str:
    first, _, rest = text.partition(" ")
    return f"{first} {first} {rest}".strip()


NOISE: dict[str, Callable[[str], str]] = {
    "shouted": str.upper,
    "no accents": _strip_accents,
    "question marks": lambda t: f"¿{t}?",
    "exclamation": lambda t: f"¡{t}!",
    "trailing ellipsis": lambda t: f"{t}...",
    "extra whitespace": lambda t: f"  {t.replace(' ', '   ')}\n",
    "leading filler": lambda t: f"eh {t}",
    "leading mmm": lambda t: f"mmm, {t}",
    "stutter": _stutter,
    "NFD unicode": lambda t: unicodedata.normalize("NFD", t),
}


@pytest.mark.parametrize("noise", NOISE, ids=list(NOISE))
@pytest.mark.parametrize("case", CASES, ids=[case["text"] for case in CASES])
def test_asr_noise_keeps_the_phrase_meaning(case: dict, noise: str) -> None:
    noisy = NOISE[noise](case["text"])
    meaning = DEFAULT_KEYTERMS.understand(noisy)
    assert list(meaning.wanted) == case["wanted"], f"{noisy!r}: {case.get('why', '')}"
    assert list(meaning.rejected) == case["rejected"], f"{noisy!r}: {case.get('why', '')}"


@pytest.mark.parametrize(
    "garbage",
    [
        "",
        " ",
        "...",
        "¿?",
        "[música]",
        "(ruido de fondo)",
        "jajajajaja",
        "mmm mmm mmm",
        "a" * 5_000,
        "🍔☕🔥",
        "\x00\x01\x02",
        "eh eh eh eh eh eh eh eh eh eh eh eh eh eh eh eh",
    ],
)
def test_hall_noise_transcripts_hear_nothing_and_never_raise(garbage: str) -> None:
    meaning = DEFAULT_KEYTERMS.understand(garbage)
    assert list(meaning.wanted) == []
    assert list(meaning.rejected) == []


def test_a_long_run_on_partial_from_a_crowd_stays_cheap() -> None:
    """A partial grows for as long as the hall is loud; the matcher runs on each one."""
    import time

    text = " ".join(["bla bla gente hablando de cualquier cosa"] * 150)  # ~1000 words
    started = time.perf_counter()
    for _ in range(20):
        DEFAULT_KEYTERMS.understand(text)
    per_call_ms = (time.perf_counter() - started) * 1000 / 20
    assert per_call_ms < 30, f"{per_call_ms:.1f} ms per partial: the hot path budget is 30 ms"
