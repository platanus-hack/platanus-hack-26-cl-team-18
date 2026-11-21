"""Replay recorded model responses (tests, offline evals, rehearsals without network).

A recording is what a provider returned for one brief: the four lines, the
latency it took, token counts, or a failure. Keyed by brief signature.
`time_scale` replays latency (0 = instant, 1 = real time).
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from app.modules.creative.application.ports.copywriter import (
    CopyFailure,
    CopyWriterError,
    TokenUsage,
    WrittenCopy,
)
from app.modules.creative.domain.brief import CreativeBrief
from app.modules.creative.domain.draft import CopyDraft, CopySource


@dataclass(frozen=True, slots=True, kw_only=True)
class Recording:
    teaser: str = ""
    offer: str = ""
    close: str = ""
    voice_line: str = ""
    latency_ms: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    failure: CopyFailure | None = None
    model_version: str = "recorded"
    confidence: Decimal | None = None

    @classmethod
    def from_json(cls, data: Mapping[str, object]) -> Recording:
        failure = data.get("failure")
        confidence = data.get("confidence")
        return cls(
            teaser=str(data.get("teaser", "")),
            offer=str(data.get("offer", "")),
            close=str(data.get("close", "")),
            voice_line=str(data.get("voice_line", "")),
            latency_ms=float(str(data.get("latency_ms", 0))),
            input_tokens=int(str(data.get("input_tokens", 0))),
            output_tokens=int(str(data.get("output_tokens", 0))),
            failure=CopyFailure(str(failure)) if failure else None,
            model_version=str(data.get("model_version", "recorded")),
            confidence=Decimal(str(confidence)) if confidence is not None else None,
        )


class RecordedCopywriter:
    name = "recorded"

    def __init__(self, recordings: Mapping[str, Recording], *, time_scale: float = 0.0) -> None:
        self._recordings = dict(recordings)
        self._time_scale = time_scale
        self.calls: list[str] = []

    def put(self, signature: str, recording: Recording) -> None:
        self._recordings[signature] = recording

    def recording_for(self, brief: CreativeBrief) -> Recording | None:
        return self._recordings.get(brief.signature())

    async def write(self, brief: CreativeBrief) -> WrittenCopy:
        signature = brief.signature()
        self.calls.append(signature)
        recording = self._recordings.get(signature)
        if recording is None:
            raise CopyWriterError(CopyFailure.UNAVAILABLE, "no recording for this brief")
        if self._time_scale > 0 and recording.latency_ms > 0:
            await asyncio.sleep(recording.latency_ms / 1000 * self._time_scale)
        if recording.failure is not None:
            raise CopyWriterError(recording.failure, "recorded failure")
        return WrittenCopy(
            draft=CopyDraft(
                teaser=recording.teaser,
                offer=recording.offer,
                close=recording.close,
                voice_line=recording.voice_line,
                source=CopySource.AI,
                model_version=recording.model_version,
                confidence=recording.confidence,
            ),
            usage=TokenUsage(
                input_tokens=recording.input_tokens,
                output_tokens=recording.output_tokens,
                estimated=False,
            ),
        )
