"""Ports for copy generation. Adapters: stock (application), recorded and Claude (infrastructure)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from app.modules.creative.domain.brief import CreativeBrief
from app.modules.creative.domain.draft import CopyDraft


@dataclass(frozen=True, slots=True, kw_only=True)
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    estimated: bool = True  # True when counted from text, not reported by the provider


@dataclass(frozen=True, slots=True, kw_only=True)
class WrittenCopy:
    draft: CopyDraft
    usage: TokenUsage = TokenUsage()


class CopyFailure(StrEnum):
    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"  # network / provider down: retryable later
    RATE_LIMITED = "rate_limited"
    QUOTA = "quota"  # out of credit: stop calling for the session
    INVALID_OUTPUT = "invalid_output"  # unparsable or schema-violating output
    REFUSED = "refused"


class CopyWriterError(Exception):
    """Provider failure translated at the adapter; raw provider text stays there."""

    def __init__(self, failure: CopyFailure, message: str = "") -> None:
        super().__init__(f"{failure}: {message}" if message else str(failure))
        self.failure = failure


class CopyWriter(Protocol):
    """Writes copy for a brief. Must not change prices or claims (the guard checks)."""

    @property
    def name(self) -> str: ...

    async def write(self, brief: CreativeBrief) -> WrittenCopy: ...


class QualityJudge(Protocol):
    """Optional second opinion on quality (live evals only): returns 0..1 and a reason."""

    async def judge(self, brief: CreativeBrief, draft: CopyDraft) -> tuple[float, str]: ...
