"""SANDBOX end to end: real processes, real sockets, a local fake ElevenLabs. No credentials.

    make test-sandbox

Processes:
  1. fake ElevenLabs   tests/sandbox/fake_elevenlabs.py   (TTS HTTP + Scribe Realtime WS)
  2. backend           uvicorn app.main:app               (simulator + speech mode + voice-overs)
  3. edge agent        python -m agent.speech --wav ...   (VAD -> Scribe client -> utterances)
This test plays the vision side (snapshots) and the player (screen socket).

Proves: snapshot -> auction -> creative on the player; the agent's speech
reaches Scribe and flips the auction to a co-brand; the voice-over is
synthesized, cached, pushed as creative.upgraded and served from /media; a
provider quota failure degrades to silent creatives; and no transcript text
lands in any process log.
"""

from __future__ import annotations

import json
import math
import os
import socket
import struct
import subprocess
import sys
import time
import wave
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import anyio
import httpx
import pytest
import websockets
from scripts.replay_audience import snapshot_for

pytestmark = [pytest.mark.sandbox, pytest.mark.timeout(90)]

ROOT = Path(__file__).resolve().parents[2]
EDGE_PYTHON = ROOT / "edge" / ".venv" / "bin" / "python"
SANDBOX_KEY = "sandbox-key"
VOICE_ID = "SandboxNarrator0001"
TRANSCRIPT = "Uf, qué hambre. Vamos por un café."

if not EDGE_PYTHON.exists():
    pytest.skip("edge venv missing: run `make edge-install`", allow_module_level=True)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@dataclass
class Proc:
    name: str
    popen: subprocess.Popen[bytes]
    log: Path

    def text(self) -> str:
        return self.log.read_text(encoding="utf-8", errors="replace")

    def stop(self) -> None:
        if self.popen.poll() is None:
            self.popen.terminate()
            try:
                self.popen.wait(5)
            except subprocess.TimeoutExpired:
                self.popen.kill()


def spawn(name: str, args: list[str], *, env: dict[str, str], cwd: Path, logs: Path) -> Proc:
    log = logs / f"{name}.log"
    clean = {k: v for k, v in os.environ.items() if not k.startswith(("ELEVENLABS", "VIRTUAL_ENV"))}
    popen = subprocess.Popen(
        args, cwd=cwd, env=clean | env, stdout=log.open("wb"), stderr=subprocess.STDOUT
    )
    return Proc(name, popen, log)


def wait_http(url: str, proc: Proc, timeout: float = 20) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.popen.poll() is not None:
            raise AssertionError(f"{proc.name} exited early:\n{proc.text()}")
        try:
            if httpx.get(url, timeout=0.5).status_code < 500:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    raise AssertionError(f"{proc.name} never became ready:\n{proc.text()}")


@dataclass
class Sandbox:
    fake_url: str
    backend_http: str
    backend_ws: str
    tmp: Path
    procs: list[Proc]

    def fake_stats(self) -> dict[str, Any]:
        return httpx.get(f"{self.fake_url}/_sandbox/stats", timeout=2).json()  # type: ignore[no-any-return]

    def agent(self, wav: Path) -> Proc:
        proc = spawn(
            "agent",
            [
                str(EDGE_PYTHON),
                "-m",
                "agent.speech",
                "--speech-mode",
                "--screen",
                "demo-01",
                "--backend",
                f"{self.backend_ws}/ws/agent",
                "--wav",
                str(wav),
                "--linger",
                "2",
                "--scribe-url",
                f"{self.fake_url.replace('http', 'ws')}/v1/speech-to-text/realtime",
            ],
            env={"ELEVENLABS_API_KEY": SANDBOX_KEY},
            cwd=ROOT / "edge",
            logs=self.tmp,
        )
        self.procs.append(proc)
        return proc


def start_sandbox(tmp: Path, *, fake_env: dict[str, str] | None = None) -> Sandbox:
    fake_port, backend_port = free_port(), free_port()
    python = sys.executable
    fake = spawn(
        "fake_elevenlabs",
        [
            python,
            "-m",
            "uvicorn",
            "tests.sandbox.fake_elevenlabs:app",
            "--port",
            str(fake_port),
            "--log-level",
            "warning",
        ],
        env={"FAKE_SCRIBE_TRANSCRIPT": TRANSCRIPT, **(fake_env or {})},
        cwd=ROOT,
        logs=tmp,
    )
    wait_http(f"http://127.0.0.1:{fake_port}/_sandbox/stats", fake)
    backend = spawn(
        "backend",
        [
            python,
            "-m",
            "uvicorn",
            "app.main:app",
            "--port",
            str(backend_port),
            "--log-level",
            "info",
        ],
        env={
            "APP_ENV": "local",
            # Explicitly override .env too: this sandbox must never use a real AI key.
            "ANTHROPIC_API_KEY": "",
            "FAL_KEY": "",
            "AUDIENCE_AI": "false",
            "CREATIVE_AI_COPY": "false",
            "VLM_ENABLED": "false",
            "VOICEOVER_PREWARM": "false",
            "DEMO_SIMULATOR": "true",
            "SPEECH_MODE_SCREENS": '["demo-01"]',
            "ELEVENLABS_API_KEY": SANDBOX_KEY,
            "ELEVENLABS_BASE_URL": f"http://127.0.0.1:{fake_port}",
            "ELEVENLABS_NARRATOR_VOICE_ID": VOICE_ID,
            "MEDIA_DIR": str(tmp / "media"),
            "ELEVENLABS_BUDGET_FILE": str(tmp / "budget.json"),
            "LOG_LEVEL": "DEBUG",
        },
        cwd=ROOT,
        logs=tmp,
    )
    wait_http(f"http://127.0.0.1:{backend_port}/health", backend)
    providers = httpx.get(f"http://127.0.0.1:{backend_port}/health", timeout=2).json()["ads"][
        "providers"
    ]
    assert not providers["audience_tags"]["configured"]
    assert not providers["ai_copy"]["configured"]
    return Sandbox(
        fake_url=f"http://127.0.0.1:{fake_port}",
        backend_http=f"http://127.0.0.1:{backend_port}",
        backend_ws=f"ws://127.0.0.1:{backend_port}",
        tmp=tmp,
        procs=[fake, backend],
    )


@pytest.fixture
def sandbox(tmp_path: Path) -> Iterator[Sandbox]:
    box = start_sandbox(tmp_path)
    try:
        yield box
    finally:
        for proc in reversed(box.procs):
            proc.stop()


def speech_wav(path: Path) -> Path:
    """0.5 s silence, 1.5 s of 'voice' (a tone the energy VAD hears), 1 s silence."""
    rate = 16_000
    voice = [
        int(math.sin(2 * math.pi * 220 * i / rate) * 0.3 * 32767) for i in range(int(1.5 * rate))
    ]
    samples = [0] * (rate // 2) + voice + [0] * rate
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    return path


Message = dict[str, Any]


class Inbox:
    """Collects every frame from a socket so assertions can wait on predicates."""

    def __init__(self, ws: websockets.ClientConnection) -> None:
        self.ws = ws
        self.messages: list[Message] = []

    async def pump(self) -> None:
        async for frame in self.ws:
            self.messages.append(json.loads(frame))

    async def wait(self, predicate: Callable[[Message], bool], timeout: float = 15) -> Message:
        with anyio.fail_after(timeout):
            while True:
                for message in self.messages:
                    if predicate(message):
                        return message
                await anyio.sleep(0.02)


def brands(message: Message) -> set[str]:
    return {slot["brand_name"] for slot in message["spec"]["slots"]}


def ready_with(*names: str) -> Callable[[Message], bool]:
    return lambda m: m["type"] == "creative.ready" and brands(m) == set(names)


def upgraded(creative_id: str) -> Callable[[Message], bool]:
    return lambda m: (
        m["type"] == "creative.upgraded"
        and m["creative_id"] == creative_id
        and m["upgrade_kind"] == "voice"
    )


def test_speech_flips_the_auction_and_voiceovers_play_end_to_end(sandbox: Sandbox) -> None:
    wav = speech_wav(sandbox.tmp / "line.wav")

    async def scenario() -> tuple[Message, Message, Message, Inbox]:
        async with (
            websockets.connect(f"{sandbox.backend_ws}/ws/screen/demo-01") as player_ws,
            websockets.connect(f"{sandbox.backend_ws}/ws/console") as console_ws,
            websockets.connect(f"{sandbox.backend_ws}/ws/agent") as vision,
            anyio.create_task_group() as tg,
        ):
            player, console = Inbox(player_ws), Inbox(console_ws)
            tg.start_soon(player.pump)
            tg.start_soon(console.pump)

            # 1. Vision: someone in gym clothes -> Voltio solo, then its voice-over.
            await vision.send(snapshot_for("fitness", 1, datetime.now(UTC)).model_dump_json())
            solo = await player.wait(ready_with("Voltio"))
            solo_voice = await player.wait(upgraded(solo["creative_id"]))

            # 2. Speech: the real agent hears "uf, qué hambre… un café" -> co-brand.
            agent = await anyio.to_thread.run_sync(sandbox.agent, wav)
            flipped = await player.wait(ready_with("Voltio", "Café Patio"), timeout=30)
            flipped_voice = await player.wait(upgraded(flipped["creative_id"]))
            await console.wait(
                lambda m: (
                    m["type"] == "console.moment"
                    and any("speech" in s["sources"] for s in m["segments"])
                )
            )
            exit_code = await anyio.to_thread.run_sync(lambda: agent.popen.wait(30))
            assert exit_code == 0, agent.text()
            tg.cancel_scope.cancel()
            return solo_voice, flipped, flipped_voice, console

    solo_voice, flipped, flipped_voice, _console = anyio.run(scenario)

    # The voice-over is real bytes from the (fake) provider, served by the backend.
    url = flipped_voice["spec"]["voiceover_url"]
    assert url.startswith("/media/voiceovers/") and url.endswith(".mp3")
    media = httpx.get(f"{sandbox.backend_http}{url}", timeout=5)
    assert media.status_code == 200 and media.content.startswith(b"ID3")
    assert flipped_voice["spec"]["slots"] == flipped["spec"]["slots"]
    assert solo_voice["spec"]["voiceover_url"] != url  # different line, different file

    stats = sandbox.fake_stats()
    assert stats["scribe_sessions"] >= 1 and stats["scribe_commits"] >= 1
    assert stats["scribe_audio_bytes"] >= int(1.5 * 16_000 * 2)  # the whole voiced segment
    query = stats["scribe_queries"][0]
    assert query["model_id"] == "scribe_v3_live" and query["language_code"] == "es"
    assert "hambre" in query["keyterms"] and len(query["keyterms"]) <= 50
    assert stats["tts_texts"] == 2 and set(stats["tts_models"]) == {"eleven_flash_v2_5"}

    # Privacy: the transcript never appears in any process log.
    for proc in sandbox.procs:
        log = proc.text()
        assert "qué hambre" not in log and "Vamos por un café" not in log, (
            f"{proc.name} leaked text"
        )
        assert SANDBOX_KEY not in log, f"{proc.name} leaked the API key"


def test_provider_quota_failure_degrades_to_silent_creatives(tmp_path: Path) -> None:
    box = start_sandbox(tmp_path, fake_env={"FAKE_TTS_FAIL": "quota"})
    try:

        async def scenario() -> tuple[Message, list[Message]]:
            async with (
                websockets.connect(f"{box.backend_ws}/ws/screen/demo-01") as player_ws,
                websockets.connect(f"{box.backend_ws}/ws/console") as console_ws,
                websockets.connect(f"{box.backend_ws}/ws/agent") as vision,
                anyio.create_task_group() as tg,
            ):
                player, console = Inbox(player_ws), Inbox(console_ws)
                tg.start_soon(player.pump)
                tg.start_soon(console.pump)
                await vision.send(snapshot_for("co-brand", 1, datetime.now(UTC)).model_dump_json())
                ready = await player.wait(ready_with("Voltio", "Café Patio"))
                await console.wait(
                    lambda m: m["type"] == "console.degradation" and "voice_fallback" in m["flags"]
                )
                await anyio.sleep(0.5)
                tg.cancel_scope.cancel()
                return ready, player.messages

        ready, messages = anyio.run(scenario)
        assert ready["spec"]["voiceover_url"] is None
        assert not any(m["type"] == "creative.upgraded" for m in messages)
        assert box.fake_stats()["tts_requests"] == 1  # quota: stop spending calls
        health = httpx.get(f"{box.backend_http}/health", timeout=2).json()
        assert health["status"] == "ok"
    finally:
        for proc in reversed(box.procs):
            proc.stop()
