"""Keyterm matching on partial transcripts (docs/stack.md §3).

Fires the intent signal on the *partial* transcript, about 100–300 ms before
the commit. Matching is accent- and case-insensitive and respects word
boundaries ("gym" doesn't match "gymkana"; "bajón" matches "bajon").

The catalog maps a canonical keyterm (sent to the backend in
`audience.utterance.keyterms_hit`) to its surface variants, including Chilean
Spanish. The backend's Audience context owns the keyterm -> IAB segment map;
the agent only reports which canonical terms were heard.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field

_NON_WORD = re.compile(r"[^a-z0-9ñ]+")


def normalize(text: str) -> str:
    """Lowercase, strip accents (keeping ñ), collapse punctuation to single spaces."""
    lowered = text.lower().replace("ñ", "\0")
    folded = "".join(
        ch for ch in unicodedata.normalize("NFKD", lowered) if not unicodedata.combining(ch)
    )
    return _NON_WORD.sub(" ", folded.replace("\0", "ñ")).strip()


def _request_clauses(text: str) -> Iterator[tuple[str, bool]]:
    """Keep additions, but make explicit corrections replace earlier wants."""
    for clause in re.split(r"[,;.!?]|\b(?:pero|but|sino)\b", text.lower()):
        start = 0
        correction = False
        for match in re.finditer(
            r"\b(?:mejor|prefiero|más bien|mas bien|en realidad|actually|rather)\b", clause
        ):
            before = normalize(clause[: match.start()]).split()
            after = normalize(clause[match.end() :]).split()
            if before and before[-1] in {"el", "la", "un", "una", "es", "son", "los", "las", "no"}:
                continue  # superlative or negated preference, not a correction
            if match.group() == "mejor" and after and after[0] == "que":
                continue  # comparison, not a replacement request
            yield clause[start : match.start()], correction
            start = match.end()
            correction = True
        yield clause[start:], correction


@dataclass(frozen=True)
class RequestMeaning:
    """Only canonical topics, never raw text. No identity or speaker inference."""

    wanted: tuple[str, ...]
    rejected: tuple[str, ...]
    ignored: tuple[str, ...] = ()


@dataclass(frozen=True)
class KeytermCatalog:
    """canonical -> variants. Every canonical is also its own variant."""

    variants: Mapping[str, tuple[str, ...]]
    _index: tuple[tuple[str, str], ...] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        index: dict[str, str] = {}
        for canonical, surfaces in self.variants.items():
            if not canonical.strip():
                raise ValueError("empty canonical keyterm")
            for surface in (canonical, *surfaces):
                norm = normalize(surface)
                if not norm:
                    raise ValueError(f"keyterm variant {surface!r} normalizes to nothing")
                owner = index.setdefault(norm, canonical)
                if owner != canonical:
                    raise ValueError(
                        f"variant {surface!r} maps to both {owner!r} and {canonical!r}"
                    )
        # Longest first, so "café con leche" wins over "café" in prompting order.
        object.__setattr__(
            self, "_index", tuple(sorted(index.items(), key=lambda kv: (-len(kv[0]), kv[0])))
        )

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, Iterable[str]]) -> KeytermCatalog:
        return cls({canonical: tuple(v) for canonical, v in mapping.items()})

    def match(self, text: str) -> tuple[str, ...]:
        """Canonical keyterms present in `text`, in catalog order, without duplicates."""
        return self.understand(text).wanted

    def understand(self, text: str) -> RequestMeaning:
        """Conservative clause matching; negation cannot become a positive bid signal.

        Delimiters and affirmative verbs reset negation, while 'ni' preserves it.
        Explicit nonalcoholic modifiers suppress the alcohol topic. Unknown words
        remain for warm interpretation; we never fuzzy-match arbitrary words.
        """
        text = re.sub(r"\b0[.,]0\b", "sin alcohol", text.lower())
        state: dict[str, bool | None] = {}
        for clause, correction in _request_clauses(text):
            if correction:
                state = {
                    canonical: False if wanted is True else wanted
                    for canonical, wanted in state.items()
                }
            words = normalize(clause).split()
            padded = " " + " ".join(words) + " "
            occurrences: dict[tuple[int, str], int] = {}
            for surface, canonical in self._index:
                for match in re.finditer(r"(?<!\S)" + re.escape(surface) + r"(?!\S)", padded):
                    start = len(padded[: match.start()].split())
                    occurrences[(start, canonical)] = len(surface.split())
            for (start, canonical), length in sorted(occurrences.items()):
                prefix = words[:start]
                prefix_text = " ".join(prefix)
                if any(
                    cue in prefix_text
                    for cue in (
                        "anuncio dice",
                        "publicidad dice",
                        "palabra",
                        "que significa",
                        "si quisiera",
                        "si tuviera",
                        "supongamos",
                        "hipoteticamente",
                    )
                ):
                    state[canonical] = None
                    continue  # let warm semantics understand background/quoted speech
                negated = any(
                    cue in prefix_text for cue in ("en vez de", "en lugar de", "instead of")
                )
                for i, word in enumerate(prefix):
                    if word == "no" and i + 1 < len(prefix) and prefix[i + 1] == "solo":
                        continue
                    if word == "sin" and i + 1 < len(prefix) and prefix[i + 1] == "alcohol":
                        continue
                    if word in {"no", "sin", "ni", "tampoco", "not", "without"}:
                        negated = True
                    elif word in {"si", "yes"}:
                        negated = False
                    elif (
                        word in {"quiero", "necesito", "want", "prefiero"}
                        and i
                        and prefix[i - 1] not in {"no", "not", "tampoco"}
                    ):
                        # A fresh affirmative request starts a new clause.
                        negated = False
                tail = " ".join(words[start + length :])
                if tail == "no" or tail.startswith("no "):
                    negated = True
                if canonical == "cerveza" and (
                    "sin alcohol" in normalize(clause)
                    or "no alcohol" in normalize(clause)
                    or tail.startswith("0 0")
                ):
                    negated = True
                state[canonical] = not negated
        return RequestMeaning(
            tuple(c for c in self.variants if state.get(c) is True),
            tuple(c for c in self.variants if state.get(c) is False),
            tuple(c for c in self.variants if c in state and state[c] is None),
        )

    def prompt_terms(self, limit: int = 50, max_chars: int = 1000) -> tuple[str, ...]:
        """Surface forms to bias the recognizer with (Scribe realtime: ≤50 terms, ≤1000 chars).

        Canonicals go first, then variants round-robin, so a tight budget still
        covers every intent before it spends terms on extra variants.
        """
        ordered: dict[str, None] = dict.fromkeys(self.variants)
        depth = max((len(v) for v in self.variants.values()), default=0)
        for i in range(depth):
            for surfaces in self.variants.values():
                if i < len(surfaces):
                    ordered.setdefault(surfaces[i], None)
        out: list[str] = []
        used = 0
        for term in ordered:
            if len(out) >= limit or used + len(term) > max_chars:
                break
            out.append(term)
            used += len(term)
        return tuple(out)


class PartialHitTracker:
    """Reports each keyterm once per utterance segment, as soon as a partial shows it."""

    def __init__(self, catalog: KeytermCatalog) -> None:
        self._catalog = catalog
        self._reported: set[str] = set()

    def on_partial(self, text: str) -> tuple[str, ...]:
        new = tuple(k for k in self._catalog.match(text) if k not in self._reported)
        self._reported.update(new)
        return new

    def on_committed(self, text: str) -> tuple[str, ...]:
        """All hits of the final text; resets for the next segment."""
        hits = self._catalog.match(text)
        self._reported.clear()
        return hits


# Demo catalog: intent words people actually say in front of a Santiago screen.
DEFAULT_KEYTERMS = KeytermCatalog.from_mapping(
    {
        "hambre": ("hambriento", "hambrienta", "bajón", "con bajón", "tengo hambre", "qué hambre"),
        "comida": ("almorzar", "almuerzo", "comer", "once", "completo", "sopaipilla"),
        "café": ("cafecito", "un café", "cortado", "capuchino", "espresso", "acfe"),
        "gym": ("gimnasio", "entrenar", "entreno", "post entreno", "crossfit"),
        "sed": ("tengo sed", "agua", "algo helado"),
        "cerveza": ("cervezas", "cervecita", "cervecitas", "chela", "chelas", "schop", "shop"),
        "energía": ("cansado", "cansada", "sueño", "energético", "energizante"),
    }
)
