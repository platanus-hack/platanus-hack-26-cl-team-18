"""ElevenLabs text-to-speech adapter (`VoiceSynthesizer`), raw httpx.

POST /v1/text-to-speech/{voice_id}?output_format=… with `xi-api-key`.
Anti-corruption layer: HTTP statuses and the provider's `detail` body are
translated into `VoiceSynthesisError(VoiceFailure)`; nothing provider-shaped
leaves this module.

Retries only what the provider says is transient (429 `rate_limit_exceeded`,
`concurrent_limit_exceeded`, `system_busy`; 5xx; network errors), with a
small bounded budget: this is warm-path work, and the creative plays fine
without a voice.
"""

from __future__ import annotations

import asyncio
import random
import re
from collections.abc import Sequence
from typing import Any

import httpx

from app.modules.creative.application.ports.voice import (
    SynthesizedVoice,
    VoiceFailure,
    VoiceRequest,
    VoiceSynthesisError,
)

DEFAULT_BASE_URL = "https://api.elevenlabs.io"
_VOICE_ID = re.compile(r"^[A-Za-z0-9]{10,40}$")

DEFAULT_SETTINGS: dict[str, Any] = {
    "stability": 0.5,
    "similarity_boost": 0.75,
    "style": 0.0,  # > 0 adds latency
    "use_speaker_boost": True,
    "speed": 1.0,
}


class ElevenLabsVoiceSynthesizer:
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        client: httpx.AsyncClient | None = None,
        timeout: httpx.Timeout | None = None,
        retry_delays: Sequence[float] = (0.4, 1.2),
        voice_settings: dict[str, Any] | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("ELEVENLABS_API_KEY is required for voice-overs")
        self._client = client or httpx.AsyncClient(
            base_url=base_url, timeout=timeout or httpx.Timeout(10.0, connect=3.0)
        )
        self._headers = {"xi-api-key": api_key, "accept": "audio/*"}
        self._retry_delays = tuple(retry_delays)
        self._settings = voice_settings or DEFAULT_SETTINGS

    async def aclose(self) -> None:
        await self._client.aclose()

    async def synthesize(self, request: VoiceRequest) -> SynthesizedVoice:
        if not _VOICE_ID.match(request.voice_id):
            raise VoiceSynthesisError(VoiceFailure.INVALID_REQUEST, "malformed voice id")
        attempt = 0
        while True:
            try:
                return await self._once(request)
            except VoiceSynthesisError as exc:
                if not exc.retryable or attempt >= len(self._retry_delays):
                    raise
                delay = self._retry_delays[attempt]
                await asyncio.sleep(delay / 2 + random.random() * delay / 2)
                attempt += 1

    async def _once(self, request: VoiceRequest) -> SynthesizedVoice:
        body = {
            "text": request.text,
            "model_id": request.model_id,
            "language_code": request.language_code,
            "voice_settings": self._settings,
            "apply_text_normalization": "auto",
        }
        try:
            response = await self._client.post(
                f"/v1/text-to-speech/{request.voice_id}",
                params={"output_format": request.output_format},
                headers=self._headers,
                json=body,
            )
        except httpx.TimeoutException as exc:
            raise VoiceSynthesisError(VoiceFailure.UNAVAILABLE, "timeout") from exc
        except httpx.TransportError as exc:
            raise VoiceSynthesisError(VoiceFailure.UNAVAILABLE, type(exc).__name__) from exc

        request_id = response.headers.get("request-id")
        if response.status_code != 200:
            raise _translate(response, request_id)
        content_type = response.headers.get("content-type", "")
        if not content_type.startswith("audio/") or not response.content:
            raise VoiceSynthesisError(
                VoiceFailure.UNAVAILABLE,
                f"unexpected payload ({content_type or 'none'})",
                request_id=request_id,
            )
        cost = response.headers.get("character-cost")
        return SynthesizedVoice(
            audio=response.content,
            content_type=content_type.split(";")[0],
            model_id=request.model_id,
            voice_id=request.voice_id,
            character_cost=int(cost) if cost and cost.isdigit() else None,
            request_id=request_id,
        )


def _detail(response: httpx.Response) -> tuple[str, str]:
    """(code, message) from `{"detail": {...}}`, `{"detail": [..422..]}` or nothing."""
    try:
        payload = response.json()
    except ValueError:
        return "", ""
    detail = payload.get("detail") if isinstance(payload, dict) else None
    if isinstance(detail, dict):
        code = str(detail.get("code") or detail.get("status") or "")
        return code, str(detail.get("message") or "")[:200]
    if isinstance(detail, list) and detail:
        first = detail[0] if isinstance(detail[0], dict) else {}
        return "validation_error", str(first.get("msg", ""))[:200]
    return "", ""


def _translate(response: httpx.Response, request_id: str | None) -> VoiceSynthesisError:
    status = response.status_code
    code, message = _detail(response)
    summary = f"HTTP {status} {code}".strip()
    # The code wins over the status: quota has historically arrived as a 401.
    if status == 402 or code in ("insufficient_credits", "quota_exceeded"):
        failure = VoiceFailure.QUOTA
    elif status in (401, 403):
        failure = VoiceFailure.AUTH
    elif status == 429:
        failure = VoiceFailure.RATE_LIMITED
    elif status in (400, 404, 422):
        failure = VoiceFailure.INVALID_REQUEST
    else:
        failure = VoiceFailure.UNAVAILABLE
    return VoiceSynthesisError(
        failure, f"{summary}: {message}" if message else summary, request_id=request_id
    )
