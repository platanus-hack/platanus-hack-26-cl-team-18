"""A local server that speaks the Scribe Realtime protocol (docs: api-reference/speech-to-text realtime).

Used by the unit tests instead of the real API: it checks auth and query
parameters, decodes the base64 PCM, and answers commits with a scripted
partial + committed transcript. It can inject provider errors.
"""

from __future__ import annotations

import asyncio
import base64
import json
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlparse

import websockets


@dataclass
class FakeScribe:
    api_key: str = "test-key"
    transcripts: list[str] = field(default_factory=lambda: ["uf, qué hambre"])
    error_after_commits: dict[int, str] = field(default_factory=dict)  # commit n -> error kind
    error_on_connect: str | None = None
    session_delay_s: float = 0.0
    withhold_commits: set[int] = field(default_factory=set)
    supplemental_commits: bool = False
    word_logprob: float = -0.5
    chunks: list[dict] = field(default_factory=list)  # type: ignore[type-arg]
    audio_bytes: int = 0
    sessions: int = 0
    queries: list[dict[str, list[str]]] = field(default_factory=list)
    keepalives: int = 0
    server: websockets.Server | None = None
    _commits: int = 0

    async def handler(self, ws: websockets.ServerConnection) -> None:
        assert ws.request is not None
        if ws.request.headers.get("xi-api-key") != self.api_key:
            await ws.send(json.dumps({"message_type": "auth_error", "error": "invalid api key"}))
            await ws.close()
            return
        self.sessions += 1
        self.queries.append(parse_qs(urlparse(ws.request.path).query))
        if self.error_on_connect:
            kind, self.error_on_connect = self.error_on_connect, None
            await ws.send(json.dumps({"message_type": kind, "error": "injected"}))
            await ws.close()
            return
        if self.session_delay_s:
            await asyncio.sleep(self.session_delay_s)
        await ws.send(
            json.dumps(
                {
                    "message_type": "session_started",
                    "session_id": f"s{self.sessions}",
                    "config": {"sample_rate": 16000},
                }
            )
        )
        async for raw in ws:
            msg = json.loads(raw)
            assert msg["message_type"] == "input_audio_chunk"
            assert msg["sample_rate"] == 16000
            pcm = base64.b64decode(msg["audio_base_64"])
            assert len(pcm) % 2 == 0, "PCM16 must be whole samples"
            self.chunks.append(msg)
            self.audio_bytes += len(pcm)
            if not any(pcm) and not msg["commit"]:
                self.keepalives += 1
            if not msg["commit"]:
                continue
            self._commits += 1
            if (kind := self.error_after_commits.get(self._commits)) is not None:
                await ws.send(json.dumps({"message_type": kind, "error": "injected"}))
                await ws.close()
                return
            if self._commits in self.withhold_commits:
                continue
            text = self.transcripts[(self._commits - 1) % len(self.transcripts)]
            words = text.split()
            await ws.send(
                json.dumps({"message_type": "partial_transcript", "text": " ".join(words[:-1])})
            )
            # Real Scribe re-sends an unchanged partial while audio keeps arriving.
            for _ in range(2):
                await ws.send(json.dumps({"message_type": "partial_transcript", "text": text}))
            await ws.send(json.dumps({"message_type": "committed_transcript", "text": text}))
            if self.supplemental_commits:
                await ws.send(
                    json.dumps(
                        {
                            "message_type": "committed_transcript_with_timestamps",
                            "text": text,
                            "words": [
                                {"text": w, "type": "word", "logprob": self.word_logprob}
                                for w in text.split()
                            ],
                        }
                    )
                )

    async def start(self) -> str:
        self.server = await websockets.serve(self.handler, "127.0.0.1", 0)
        port = next(iter(self.server.sockets)).getsockname()[1]
        return f"ws://127.0.0.1:{port}/v1/speech-to-text/realtime"

    async def stop(self) -> None:
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()


async def wait_for(predicate, timeout: float = 3.0) -> None:  # type: ignore[no-untyped-def]
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.005)
