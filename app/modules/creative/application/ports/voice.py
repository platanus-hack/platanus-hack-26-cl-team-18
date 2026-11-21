"""Ports for voice-overs (ADR 0005). Adapters live in creative/infrastructure."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol


@dataclass(frozen=True, slots=True)
class VoiceRequest:
    text: str
    voice_id: str
    model_id: str
    language_code: str
    output_format: str


@dataclass(frozen=True, slots=True)
class SynthesizedVoice:
    audio: bytes = field(repr=False)
    content_type: str
    model_id: str
    voice_id: str
    character_cost: int | None = None
    request_id: str | None = None


class VoiceFailure(StrEnum):
    AUTH = "auth"  # bad key / no access to the model: fatal until a human acts
    QUOTA = "quota"  # out of characters: fatal for the session
    RATE_LIMITED = "rate_limited"  # retryable
    INVALID_REQUEST = "invalid_request"  # our bug or a bad voice id
    UNAVAILABLE = "unavailable"  # provider/network down: retryable


class VoiceSynthesisError(Exception):
    """Provider failure translated at the adapter; raw provider strings stay there."""

    def __init__(
        self, failure: VoiceFailure, message: str, *, request_id: str | None = None
    ) -> None:
        super().__init__(f"{failure}: {message}")
        self.failure = failure
        self.request_id = request_id

    @property
    def retryable(self) -> bool:
        return self.failure in (VoiceFailure.RATE_LIMITED, VoiceFailure.UNAVAILABLE)


class VoiceSynthesizer(Protocol):
    async def synthesize(self, request: VoiceRequest) -> SynthesizedVoice: ...


class MediaStore(Protocol):
    """Content-addressed media cache served at /media (docs/stack.md §4)."""

    def url_if_cached(self, key: str, extension: str) -> str | None: ...

    def put(self, key: str, extension: str, data: bytes) -> str:
        """Write atomically and return the public URL."""
        ...
