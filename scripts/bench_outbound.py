"""Compare hub delivery and voice admission under controlled backpressure (no hardware/providers).

uv run python scripts/bench_outbound.py --out evals/results/outbound.json
Use --source-root to measure another checkout with the identical probe.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--slow-ms", type=float, default=120)
    parser.add_argument("--runs", type=int, default=10)
    parser.add_argument("--voice-hold-ms", type=float, default=200)
    parser.add_argument("--out", type=Path, default=Path("evals/results/outbound.json"))
    args = parser.parse_args()
    sys.path.insert(0, str(args.source_root))
    from app.contract.messages import ErrorMessage
    from app.realtime.hub import ConnectionHub

    async def trial():
        class Sink:
            def __init__(self, slow=False):
                self.slow = slow
                self.entered = asyncio.Event()
                self.arrivals = {}
                self.active = self.peak = 0

            async def send_text(self, text):
                self.active += 1
                self.peak = max(self.peak, self.active)
                self.entered.set()
                try:
                    if self.slow:
                        await asyncio.sleep(args.slow_ms / 1000)
                    self.arrivals[json.loads(text)["code"]] = time.perf_counter()
                finally:
                    self.active -= 1

        def msg(code):
            return ErrorMessage(code=code, message="synthetic benchmark")

        hub = ConnectionHub()
        slow, fast = Sink(True), Sink()
        hub.add_console(slow)
        hub.add_console(fast)
        start = time.perf_counter()
        first = asyncio.create_task(
            hub.broadcast_console(msg("moment"), msg("auction"), msg("ready"))
        )
        await slow.entered.wait()
        second = asyncio.create_task(hub.broadcast_console(msg("next")))
        await asyncio.gather(first, second)
        player = Sink(True)
        hub.add_player("probe", player)
        first = asyncio.create_task(hub.send_to_screen(msg("ready"), screen_id="probe"))
        await player.entered.wait()
        second = asyncio.create_task(hub.send_to_screen(msg("upgrade"), screen_id="probe"))
        await asyncio.gather(first, second)
        return {
            "healthy_batch_ms": (fast.arrivals["ready"] - start) * 1000,
            "healthy_next_decision_ms": (fast.arrivals["next"] - start) * 1000,
            "player_concurrent_writes": player.peak,
        }

    async def voice_trial():
        import tempfile

        from app.modules.creative.application.ports.voice import SynthesizedVoice
        from app.modules.creative.application.voiceover import VoiceoverService, VoiceProfile
        from app.modules.creative.infrastructure.media_store import FileMediaStore
        from app.realtime.warm_worker import WarmWorker

        worker = WarmWorker({"elevenlabs": 4})
        release, started = asyncio.Event(), asyncio.Event()
        calls, delivered = [], []
        admitted = {}

        class Synth:
            async def synthesize(self, request):
                calls.append(request.text)
                admitted[request.text] = time.perf_counter()
                if request.text == "Shared offer":
                    started.set()
                    await release.wait()
                return SynthesizedVoice(
                    audio=b"ID3fake",
                    content_type="audio/mpeg",
                    model_id=request.model_id,
                    voice_id=request.voice_id,
                )

        with tempfile.TemporaryDirectory() as tmp:
            service = VoiceoverService(
                Synth(), FileMediaStore(Path(tmp)), VoiceProfile("fake", "es", "mp3_44100_128")
            )

            async def request(screen, line):
                subscribe = getattr(service, "subscribe_pending", None)
                if subscribe is not None and subscribe(
                    line, "narrator", consumer=screen, on_ready=delivered.append
                ):
                    return
                delivered.append(await service.voiceover_url(line, "narrator"))

            worker.submit("elevenlabs", "first", lambda: request("first", "Shared offer"))
            await started.wait()
            for i in range(3):
                worker.submit("elevenlabs", str(i), lambda i=i: request(str(i), "Shared offer"))
            asked = time.perf_counter()
            worker.submit("elevenlabs", "fresh", lambda: request("fresh", "Different offer"))
            timer = asyncio.get_running_loop().call_later(args.voice_hold_ms / 1000, release.set)
            try:
                await worker.drain()
            finally:
                timer.cancel()
            return {
                "voice_different_offer_admission_ms": (admitted["Different offer"] - asked) * 1000,
                "voice_provider_calls": len(calls),
                "voice_results_delivered": len(delivered),
            }

    async def run():
        results = []
        for _ in range(args.runs):
            results.append({**await trial(), **await voice_trial()})
        return results

    samples = asyncio.run(run())
    report = {
        "scope": "Controlled slow sinks through the real hub; not network or physical display percentiles",
        "source_root": str(args.source_root),
        "slow_ms_per_send": args.slow_ms,
        "runs": args.runs,
        "median": {key: round(statistics.median(s[key] for s in samples), 3) for key in samples[0]},
        "samples": samples,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({key: value for key, value in report.items() if key != "samples"}, indent=2))


if __name__ == "__main__":
    main()
