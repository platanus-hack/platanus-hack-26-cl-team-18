"""Voice-overs for creatives (docs/architecture.md §7.3, ADR 0005).

- The spoken line is the creative's approved copy, validated and short.
- Cached by `(copy_hash, voice_id, model)` (+ format, language) as a file in
  the media cache: a hit costs no characters and plays instantly.
- Runs on the warm path only; a failure means "no voice", never a blank or a
  blocked creative.
"""

from __future__ import annotations

import hashlib
import logging
import re
import unicodedata
from dataclasses import dataclass

from app.modules.creative.application.ports.voice import (
    MediaStore,
    VoiceFailure,
    VoiceRequest,
    VoiceSynthesisError,
    VoiceSynthesizer,
)

logger = logging.getLogger(__name__)

MAX_WORDS = 20
MAX_CHARS = 200
_EXTENSIONS = {"mp3": "mp3", "pcm": "pcm", "opus": "opus", "wav": "wav"}


class InvalidSpokenLine(ValueError):
    pass


def spoken_line(copy: str) -> str:
    """Normalize ad copy into a line worth speaking; reject what isn't."""
    line = unicodedata.normalize("NFC", re.sub(r"\s+", " ", copy)).strip()
    # Brand separators ("Voltio · Energía…") read badly aloud.
    line = line.replace(" · ", ". ").replace("·", ". ")
    if not line:
        raise InvalidSpokenLine("empty copy")
    if len(line) > MAX_CHARS or len(line.split()) > MAX_WORDS:
        raise InvalidSpokenLine("copy too long to speak")
    return line


@dataclass(frozen=True, slots=True)
class VoiceProfile:
    model_id: str
    language_code: str
    output_format: str  # e.g. mp3_44100_128 (Creator tier)

    @property
    def extension(self) -> str:
        return _EXTENSIONS.get(self.output_format.split("_", 1)[0], "bin")


def cache_key(line: str, voice_id: str, profile: VoiceProfile) -> str:
    material = "\x1f".join(
        (line, voice_id, profile.model_id, profile.language_code, profile.output_format)
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


@dataclass
class VoiceoverStats:
    cache_hits: int = 0
    synthesized: int = 0
    characters: int = 0
    failures: int = 0
    disabled_by: VoiceFailure | None = None


class VoiceoverService:
    def __init__(
        self, synthesizer: VoiceSynthesizer, store: MediaStore, profile: VoiceProfile
    ) -> None:
        self._synthesizer = synthesizer
        self._store = store
        self._profile = profile
        self.stats = VoiceoverStats()

    def key_for(self, copy: str, voice_id: str) -> str:
        return cache_key(spoken_line(copy), voice_id, self._profile)

    def cached_url(self, copy: str, voice_id: str) -> str | None:
        """Hot-path safe: a filesystem check, no network."""
        try:
            return self._store.url_if_cached(self.key_for(copy, voice_id), self._profile.extension)
        except InvalidSpokenLine:
            return None

    async def voiceover_url(self, copy: str, voice_id: str) -> str | None:
        """Warm path: cached URL, or synthesize + store. None means 'play without voice'."""
        if self.stats.disabled_by is not None:
            return None
        try:
            line = spoken_line(copy)
        except InvalidSpokenLine as exc:
            logger.info("voice-over skipped: %s", exc)
            return None
        key = cache_key(line, voice_id, self._profile)
        if (url := self._store.url_if_cached(key, self._profile.extension)) is not None:
            self.stats.cache_hits += 1
            return url
        request = VoiceRequest(
            text=line,
            voice_id=voice_id,
            model_id=self._profile.model_id,
            language_code=self._profile.language_code,
            output_format=self._profile.output_format,
        )
        try:
            voice = await self._synthesizer.synthesize(request)
        except VoiceSynthesisError as exc:
            self.stats.failures += 1
            if exc.failure in (VoiceFailure.AUTH, VoiceFailure.QUOTA):
                # Stop spending calls that will all fail; the console shows degradation.
                self.stats.disabled_by = exc.failure
            logger.warning(
                "voice-over failed",
                extra={"source": exc.failure.value, "request_id": exc.request_id},
            )
            return None
        self.stats.synthesized += 1
        self.stats.characters += voice.character_cost or len(line)
        return self._store.put(key, self._profile.extension, voice.audio)
