"""Measure event-loop lag during controlled voice storage stalls; never calls live APIs.

uv run python scripts/bench_voice_storage.py --source-root /path/to/checkout
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import tempfile
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--delay-ms", type=float, default=150)
    parser.add_argument("--runs", type=int, default=10)
    parser.add_argument("--out", type=Path, default=Path("evals/results/voice-storage.json"))
    args = parser.parse_args()
    if args.runs < 1 or args.delay_ms <= 0:
        parser.error("runs and delay must be positive")
    sys.path.insert(0, str(args.source_root))
    from app.modules.creative.application.ports.voice import SynthesizedVoice
    from app.modules.creative.application.voiceover import VoiceoverService, VoiceProfile
    from app.modules.creative.infrastructure.character_budget import FileCharacterBudget
    from app.modules.creative.infrastructure.media_store import FileMediaStore

    async def trial(stage):
        loop = asyncio.get_running_loop()
        observed = loop.create_future()
        started = []

        def stall(operation):
            if operation == stage:
                started.append(time.perf_counter())
                loop.call_soon_threadsafe(lambda: observed.set_result(time.perf_counter()))
                time.sleep(args.delay_ms / 1000)

        class Store(FileMediaStore):
            def url_if_cached(self, key, extension):
                stall("lookup")
                return super().url_if_cached(key, extension)

            def put(self, key, extension, data):
                stall("put")
                return super().put(key, extension, data)

        class Budget(FileCharacterBudget):
            def remaining(self):
                stall("remaining")
                return super().remaining()

            def spend(self, characters):
                stall("spend")
                super().spend(characters)

        class Synth:
            async def synthesize(self, request):
                return SynthesizedVoice(
                    audio=b"ID3fake",
                    content_type="audio/mpeg",
                    model_id=request.model_id,
                    voice_id=request.voice_id,
                )

        with tempfile.TemporaryDirectory() as tmp:
            service = VoiceoverService(
                Synth(),
                Store(Path(tmp)),
                VoiceProfile("fake", "es", "mp3_44100_128"),
                Budget(Path(tmp) / "budget.json", limit=1000),
            )
            task = asyncio.create_task(service.voiceover_url("Approved offer", "narrator"))
            delivered = await observed
            await task
            return (delivered - started[0]) * 1000

    async def run():
        return {
            stage: [await trial(stage) for _ in range(args.runs)]
            for stage in ("lookup", "put", "remaining", "spend")
        }

    samples = asyncio.run(run())
    report = {
        "scope": "Controlled storage stalls through real voice service and file adapters; not ordinary disk or production percentiles",
        "source_root": str(args.source_root),
        "delay_ms": args.delay_ms,
        "runs": args.runs,
        "event_loop_lag_median_ms": {
            stage: round(statistics.median(values), 3) for stage, values in samples.items()
        },
        "samples": samples,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({key: value for key, value in report.items() if key != "samples"}, indent=2))


if __name__ == "__main__":
    main()
