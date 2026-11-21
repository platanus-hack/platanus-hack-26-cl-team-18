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
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

_NON_WORD = re.compile(r"[^a-z0-9ñ]+")


def normalize(text: str) -> str:
    """Lowercase, strip accents (keeping ñ), collapse punctuation to single spaces."""
    lowered = text.lower().replace("ñ", "\0")
    folded = "".join(
        ch for ch in unicodedata.normalize("NFKD", lowered) if not unicodedata.combining(ch)
    )
    return _NON_WORD.sub(" ", folded.replace("\0", "ñ")).strip()


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
        padded = f" {normalize(text)} "
        hits: dict[str, None] = {}
        for surface, canonical in self._index:
            if f" {surface} " in padded:
                hits[canonical] = None
        return tuple(c for c in self.variants if c in hits)

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
        "café": ("cafecito", "un café", "cortado", "capuchino", "espresso"),
        "gym": ("gimnasio", "entrenar", "entreno", "post entreno", "crossfit"),
        "sed": ("tengo sed", "agua", "algo helado"),
        "cerveza": ("chela", "chelas", "schop", "shop"),
        "energía": ("cansado", "cansada", "sueño", "energético", "energizante"),
    }
)
