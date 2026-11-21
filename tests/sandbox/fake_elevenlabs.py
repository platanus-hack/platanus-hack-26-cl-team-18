"""A local stand-in for the ElevenLabs API, for sandbox integration runs.

    uv run uvicorn tests.sandbox.fake_elevenlabs:app --port 8790

It implements the two surfaces we use, with the documented shapes:

- `POST /v1/text-to-speech/{voice_id}?output_format=...` -> audio/mpeg
- `GET /v2/voices` -> a premade Spanish voice
- `WS /v1/speech-to-text/realtime` (Scribe Realtime protocol): session_started,
  then a partial + committed transcript per manual commit

Auth uses the `xi-api-key` header (`SANDBOX_KEY`). Behaviour is set by env:
`FAKE_TTS_FAIL=quota|rate_limited|auth`, `FAKE_SCRIBE_TRANSCRIPT=...`.
`GET /_sandbox/stats` reports what was called (never audio or text).
"""

from __future__ import annotations

import base64
import json
import os
from typing import Any

from fastapi import FastAPI, Header, Request, Response, WebSocket
from fastapi.responses import JSONResponse

SANDBOX_KEY = "sandbox-key"
VOICE_ID = "SandboxNarrator0001"
MP3 = b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\xff\xfb\x90\x00" * 64  # header + silent frames

app = FastAPI()
stats: dict[str, Any] = {
    "tts_requests": 0,
    "tts_texts": 0,
    "tts_models": [],
    "scribe_sessions": 0,
    "scribe_audio_bytes": 0,
    "scribe_commits": 0,
    "scribe_keepalives": 0,
    "scribe_queries": [],
}


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={
            "detail": {"type": "sandbox", "code": code, "message": message, "request_id": "sbx"}
        },
    )


@app.post("/v1/text-to-speech/{voice_id}")
async def tts(
    voice_id: str,
    request: Request,
    output_format: str = "mp3_44100_128",
    xi_api_key: str | None = Header(default=None),
) -> Response:
    if xi_api_key != SANDBOX_KEY:
        return _error(401, "invalid_api_key", "Invalid API key")
    stats["tts_requests"] += 1
    if (fail := os.environ.get("FAKE_TTS_FAIL")) == "quota":
        return _error(402, "insufficient_credits", "out of characters")
    if fail == "rate_limited":
        return _error(429, "system_busy", "busy")
    if fail == "auth":
        return _error(401, "invalid_api_key", "revoked")
    if voice_id != VOICE_ID:
        return _error(404, "voice_not_found", "voice not found")
    if output_format != "mp3_44100_128":
        return _error(403, "output_format_not_allowed", "format needs a higher tier")
    body = await request.json()
    if not isinstance(body.get("text"), str) or not body["text"].strip():
        return JSONResponse(
            status_code=422, content={"detail": [{"loc": ["body", "text"], "msg": "required"}]}
        )
    if body.get("model_id") != "eleven_flash_v2_5" or body.get("language_code") != "es":
        return _error(400, "invalid_request", "unexpected model or language")
    stats["tts_texts"] += 1
    stats["tts_models"].append(body["model_id"])
    return Response(
        MP3,
        media_type="audio/mpeg",
        headers={
            "request-id": f"sbx-{stats['tts_requests']}",
            "character-cost": str(len(body["text"])),
        },
    )


@app.get("/v2/voices")
async def voices(xi_api_key: str | None = Header(default=None)) -> Response:
    if xi_api_key != SANDBOX_KEY:
        return _error(401, "invalid_api_key", "Invalid API key")
    return JSONResponse(
        {
            "voices": [
                {
                    "voice_id": VOICE_ID,
                    "name": "Sandbox Narradora",
                    "category": "premade",
                    "labels": {"gender": "female"},
                    "verified_languages": [{"language": "es", "accent": "latin american"}],
                }
            ],
            "has_more": False,
        }
    )


@app.websocket("/v1/speech-to-text/realtime")
async def scribe(ws: WebSocket) -> None:
    await ws.accept()
    if ws.headers.get("xi-api-key") != SANDBOX_KEY:
        await ws.send_text(json.dumps({"message_type": "auth_error", "error": "invalid api key"}))
        await ws.close()
        return
    query = dict(ws.query_params.multi_items())
    stats["scribe_queries"].append(
        {k: v for k, v in query.items() if k != "keyterms"}
        | {"keyterms": ws.query_params.getlist("keyterms")}
    )
    if query.get("audio_format") != "pcm_16000" or query.get("commit_strategy") != "manual":
        await ws.send_text(json.dumps({"message_type": "invalid_request", "error": "bad query"}))
    stats["scribe_sessions"] += 1
    await ws.send_text(
        json.dumps({"message_type": "session_started", "session_id": "sbx", "config": query})
    )
    transcript = os.environ.get("FAKE_SCRIBE_TRANSCRIPT", "Uf, qué hambre. Vamos por un café.")
    scripted = json.loads(os.environ.get("FAKE_SCRIBE_TRANSCRIPTS", "[]"))
    commit_index = 0
    while True:
        try:
            msg = json.loads(await ws.receive_text())
        except Exception:
            return
        if msg.get("message_type") != "input_audio_chunk" or msg.get("sample_rate") != 16000:
            await ws.send_text(json.dumps({"message_type": "input_error", "error": "bad chunk"}))
            continue
        pcm = base64.b64decode(msg["audio_base_64"])
        stats["scribe_audio_bytes"] += len(pcm)
        if not any(pcm) and not msg.get("commit"):
            stats["scribe_keepalives"] += 1
        if msg.get("commit"):
            stats["scribe_commits"] += 1
            if scripted:
                transcript = scripted[min(commit_index, len(scripted) - 1)]
                commit_index += 1
            words = transcript.split()
            await ws.send_text(
                json.dumps({"message_type": "partial_transcript", "text": " ".join(words[:3])})
            )
            await ws.send_text(
                json.dumps({"message_type": "partial_transcript", "text": transcript})
            )
            await ws.send_text(
                json.dumps({"message_type": "committed_transcript", "text": transcript})
            )


@app.get("/_sandbox/stats")
async def sandbox_stats() -> dict[str, Any]:
    return stats
