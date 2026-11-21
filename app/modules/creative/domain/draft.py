"""Copy for the three dwell stages, plus where it came from (AI provenance)."""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from enum import StrEnum


class Stage(StrEnum):
    TEASER = "teaser"  # 0–3 s: video loop + logos, big type
    OFFER = "offer"  # 3–8 s: the basket offer sized to the group
    CLOSE = "close"  # 8 s+: QR + "valid 10 min"


class CopySource(StrEnum):
    STOCK = "stock"  # deterministic templates over approved brand-kit copy
    AI = "ai"  # a model wrote it, the guard passed it
    CACHE = "cache"  # an AI draft reused for an identical brief


@dataclass(frozen=True, slots=True, kw_only=True)
class CopyDraft:
    teaser: str
    offer: str
    close: str
    voice_line: str
    source: CopySource
    model_version: str | None = None
    confidence: Decimal | None = None  # 0..1, the model's own; never truth by itself
    reasoning: str = ""

    def line(self, stage: Stage) -> str:
        return {Stage.TEASER: self.teaser, Stage.OFFER: self.offer, Stage.CLOSE: self.close}[stage]

    def stages(self) -> tuple[tuple[Stage, str], ...]:
        return tuple((stage, self.line(stage)) for stage in Stage)

    def all_text(self) -> str:
        return " \n".join((self.teaser, self.offer, self.close, self.voice_line))

    def cached(self) -> CopyDraft:
        return replace(self, source=CopySource.CACHE)
