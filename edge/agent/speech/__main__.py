"""Run speech mode: `uv run python -m agent.speech --screen demo-01 --speech-mode` (from edge/).

This is the agent's composition root for speech: it picks the audio source,
the VAD and the transcriber. Speech mode is OFF unless `--speech-mode` is
passed (ADR 0005: opt-in per screen, with a visible listening indicator).

Environment: ELEVENLABS_API_KEY (required), read from the environment or edge/.env.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import os
import sys
from pathlib import Path

from agent.backend_link import BackendLink
from agent.speech.keyterms import DEFAULT_KEYTERMS
from agent.speech.pipeline import SpeechPipeline
from agent.speech.scribe import DEFAULT_URL, ScribeConfig, ScribeFatalError, ScribeRealtime
from agent.speech.sources import MicSource, WavSource
from agent.speech.vad import EnergyVad, SileroVad, SpeechGate, VoiceActivityDetector

logger = logging.getLogger("agent.speech")


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition("=")
        if sep and not key.startswith("#") and key.strip() not in os.environ:
            os.environ[key.strip()] = value.strip().strip('"').strip("'")


def _vad(model: Path | None) -> VoiceActivityDetector:
    if model is not None:
        return SileroVad(model)
    logger.warning("no --silero-model given: using the energy VAD (fine for a quiet room only)")
    return EnergyVad()


async def run(args: argparse.Namespace) -> int:
    _load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    if not args.speech_mode:
        print("speech mode is off (pass --speech-mode on an opted-in screen)", file=sys.stderr)
        return 2
    config = ScribeConfig(
        api_key=os.environ.get("ELEVENLABS_API_KEY", ""),
        model_id=args.model,
        language_code=args.language,
        url=args.scribe_url or os.environ.get("ELEVENLABS_STT_URL") or DEFAULT_URL,
        keyterms=DEFAULT_KEYTERMS.prompt_terms(),
    )
    link = BackendLink(args.backend)
    pipeline: SpeechPipeline | None = None
    scribe = ScribeRealtime(config, lambda event: pipeline.on_event(event) if pipeline else None)
    pipeline = SpeechPipeline(
        screen_id=args.screen,
        gate=SpeechGate(_vad(args.silero_model)),
        transcriber=scribe,
        keyterms=DEFAULT_KEYTERMS,
        emit=link.send,
        language=args.language,
    )
    source = WavSource(args.wav) if args.wav else MicSource(device=args.device)
    print(f"● LISTENING on screen {args.screen} (speech mode on)", file=sys.stderr)

    async def feed() -> None:
        async for frame in source.frames():
            pipeline.on_frame(frame)
        if args.wav:
            await asyncio.sleep(args.linger)  # let the last transcript arrive

    tasks = [asyncio.create_task(link.run()), asyncio.create_task(scribe.run())]
    try:
        feeder = asyncio.create_task(feed())
        done, _ = await asyncio.wait([feeder, *tasks], return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
        return 0
    except ScribeFatalError as exc:
        print(f"speech degraded: {exc}", file=sys.stderr)
        return 1
    finally:
        for task in tasks:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        print(f"○ stopped listening ({pipeline.stats})", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(prog="agent.speech")
    parser.add_argument("--screen", default="demo-01")
    parser.add_argument("--backend", default="ws://127.0.0.1:8000/ws/agent")
    parser.add_argument(
        "--speech-mode", action="store_true", help="explicit opt-in for this screen"
    )
    parser.add_argument("--wav", type=Path, help="replay a 16 kHz mono WAV instead of the mic")
    parser.add_argument("--device", default=None, help="sounddevice input device")
    parser.add_argument("--silero-model", type=Path, default=None)
    parser.add_argument("--model", default="scribe_v2_realtime")
    parser.add_argument(
        "--scribe-url", default=None, help="override (sandbox); env ELEVENLABS_STT_URL"
    )
    parser.add_argument("--language", default="es")
    parser.add_argument("--linger", type=float, default=3.0)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
