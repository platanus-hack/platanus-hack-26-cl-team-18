"""The TTS adapter against recorded ElevenLabs HTTP shapes (httpx.MockTransport, no network)."""

from __future__ import annotations

import json

import anyio
import httpx
import pytest

from app.modules.creative.application.ports.voice import (
    VoiceFailure,
    VoiceRequest,
    VoiceSynthesisError,
)
from app.modules.creative.infrastructure.elevenlabs_tts import ElevenLabsVoiceSynthesizer

VOICE_ID = "JBFqnCBsd6RMkjVDRZzb"
MP3 = b"ID3\x04\x00fake-mp3-frames"
REQUEST = VoiceRequest(
    text="Combo post-entreno. Voltio y Café Patio.",
    voice_id=VOICE_ID,
    model_id="eleven_flash_v2_5",
    language_code="es",
    output_format="mp3_44100_128",
)


def adapter(
    *responses: httpx.Response, seen: list[httpx.Request] | None = None
) -> ElevenLabsVoiceSynthesizer:
    queue = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return queue.pop(0) if len(queue) > 1 else queue[0]

    client = httpx.AsyncClient(
        base_url="https://api.elevenlabs.io", transport=httpx.MockTransport(handler)
    )
    return ElevenLabsVoiceSynthesizer("sk_test", client=client, retry_delays=(0.0, 0.0))


def audio_ok() -> httpx.Response:
    return httpx.Response(
        200,
        content=MP3,
        headers={"content-type": "audio/mpeg", "request-id": "req-1", "character-cost": "41"},
    )


def error(status: int, code: str, message: str = "x") -> httpx.Response:
    return httpx.Response(
        status,
        json={"detail": {"type": "t", "code": code, "message": message, "request_id": "req-e"}},
    )


def synth(a: ElevenLabsVoiceSynthesizer, request: VoiceRequest = REQUEST):  # type: ignore[no-untyped-def]
    return anyio.run(a.synthesize, request)


def test_sends_the_documented_request_and_returns_audio() -> None:
    seen: list[httpx.Request] = []
    voice = synth(adapter(audio_ok(), seen=seen))
    (req,) = seen
    assert req.method == "POST"
    assert req.url.path == f"/v1/text-to-speech/{VOICE_ID}"
    assert req.url.params["output_format"] == "mp3_44100_128"
    assert req.headers["xi-api-key"] == "sk_test"
    body = json.loads(req.content)
    assert body["model_id"] == "eleven_flash_v2_5" and body["language_code"] == "es"
    assert body["text"] == REQUEST.text
    assert body["voice_settings"]["style"] == 0.0
    assert voice.audio == MP3 and voice.content_type == "audio/mpeg"
    assert voice.character_cost == 41 and voice.request_id == "req-1"
    assert "sk_test" not in repr(voice)


@pytest.mark.parametrize(
    ("response", "failure"),
    [
        (error(401, "invalid_api_key"), VoiceFailure.AUTH),
        (error(403, "model_access_denied"), VoiceFailure.AUTH),
        (error(402, "insufficient_credits"), VoiceFailure.QUOTA),
        (error(401, "quota_exceeded"), VoiceFailure.QUOTA),
        (error(404, "voice_not_found"), VoiceFailure.INVALID_REQUEST),
        (
            httpx.Response(
                422, json={"detail": [{"loc": ["body", "text"], "msg": "field required"}]}
            ),
            VoiceFailure.INVALID_REQUEST,
        ),
        (error(429, "rate_limit_exceeded"), VoiceFailure.RATE_LIMITED),
        (error(429, "concurrent_limit_exceeded"), VoiceFailure.RATE_LIMITED),
        (error(503, "service_unavailable"), VoiceFailure.UNAVAILABLE),
        (httpx.Response(500, text="<html>oops</html>"), VoiceFailure.UNAVAILABLE),
        (httpx.Response(200, json={"not": "audio"}), VoiceFailure.UNAVAILABLE),
    ],
)
def test_provider_errors_are_translated(response: httpx.Response, failure: VoiceFailure) -> None:
    with pytest.raises(VoiceSynthesisError) as exc:
        synth(adapter(response))
    assert exc.value.failure is failure


def test_transient_errors_are_retried_within_a_budget() -> None:
    seen: list[httpx.Request] = []
    voice = synth(adapter(error(429, "system_busy"), error(503, "x"), audio_ok(), seen=seen))
    assert voice.audio == MP3 and len(seen) == 3


def test_retry_budget_is_bounded() -> None:
    seen: list[httpx.Request] = []
    with pytest.raises(VoiceSynthesisError):
        synth(adapter(error(429, "system_busy"), seen=seen))
    assert len(seen) == 3  # 1 + 2 retries, then give up: the creative plays without voice


def test_fatal_errors_are_never_retried() -> None:
    seen: list[httpx.Request] = []
    with pytest.raises(VoiceSynthesisError):
        synth(adapter(error(401, "invalid_api_key"), seen=seen))
    assert len(seen) == 1


def test_network_failures_are_unavailable() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("slow", request=request)

    client = httpx.AsyncClient(base_url="https://x", transport=httpx.MockTransport(boom))
    a = ElevenLabsVoiceSynthesizer("k", client=client, retry_delays=())
    with pytest.raises(VoiceSynthesisError) as exc:
        synth(a)
    assert exc.value.failure is VoiceFailure.UNAVAILABLE


def test_malformed_voice_id_never_reaches_the_url() -> None:
    seen: list[httpx.Request] = []
    bad = VoiceRequest(
        text="x",
        voice_id="../v1/user",
        model_id="m",
        language_code="es",
        output_format="mp3_44100_128",
    )
    with pytest.raises(VoiceSynthesisError) as exc:
        synth(adapter(audio_ok(), seen=seen), bad)
    assert exc.value.failure is VoiceFailure.INVALID_REQUEST and seen == []


def test_requires_an_api_key() -> None:
    with pytest.raises(ValueError):
        ElevenLabsVoiceSynthesizer("")
