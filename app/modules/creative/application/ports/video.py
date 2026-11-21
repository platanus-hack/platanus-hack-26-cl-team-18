"""Text-to-video for pre-generated bundle loops (cold path; never the hot path, ADR 0003)."""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol


class VideoFailure(StrEnum):
    NOT_CONFIGURED = "not_configured"
    UNAVAILABLE = "unavailable"
    REJECTED = "rejected"
    TIMEOUT = "timeout"
    INVALID_OUTPUT = "invalid_output"


class VideoGenerationError(Exception):
    def __init__(self, failure: VideoFailure, detail: str = "") -> None:
        super().__init__(f"{failure.value}: {detail}" if detail else failure.value)
        self.failure = failure


class VideoGenerator(Protocol):
    name: str
    model: str

    async def generate(self, prompt: str, *, duration_s: int) -> bytes:
        """An MP4 clip. Prompts describe motion only: no text, no logos (ADR 0003)."""
        ...
