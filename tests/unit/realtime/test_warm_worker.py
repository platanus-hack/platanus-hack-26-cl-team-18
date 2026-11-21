from __future__ import annotations

import asyncio

import anyio

from app.realtime.warm_worker import WarmWorker


def test_same_key_in_flight_runs_once() -> None:
    async def run() -> tuple[int, WarmWorker]:
        worker = WarmWorker()
        calls = 0
        gate = asyncio.Event()

        async def job() -> None:
            nonlocal calls
            calls += 1
            await gate.wait()

        assert worker.submit("elevenlabs", "copy-1|voice-a", job)
        assert not worker.submit("elevenlabs", "copy-1|voice-a", job)
        gate.set()
        await worker.drain()
        # once finished, the key can run again (a cache miss later is a new job)
        assert worker.submit("elevenlabs", "copy-1|voice-a", job)
        await worker.drain()
        return calls, worker

    calls, worker = anyio.run(run)
    assert calls == 2
    assert worker.stats.deduped == 1 and worker.stats.completed == 2


def test_concurrency_is_bounded_per_provider() -> None:
    async def run() -> tuple[int, int]:
        worker = WarmWorker({"fal": 2, "claude": 1})
        running = {"fal": 0, "claude": 0}
        peak = {"fal": 0, "claude": 0}

        def make(provider: str):
            async def job() -> None:
                running[provider] += 1
                peak[provider] = max(peak[provider], running[provider])
                await asyncio.sleep(0.01)
                running[provider] -= 1

            return job

        for i in range(6):
            worker.submit("fal", f"img-{i}", make("fal"))
            worker.submit("claude", f"copy-{i}", make("claude"))
        await worker.drain()
        return peak["fal"], peak["claude"]

    assert anyio.run(run) == (2, 1)


def test_a_failing_job_is_isolated_and_counted() -> None:
    async def run() -> WarmWorker:
        worker = WarmWorker()
        ok: list[str] = []

        async def boom() -> None:
            raise RuntimeError("provider 500")

        async def fine() -> None:
            ok.append("done")

        worker.submit("fal", "a", boom)
        worker.submit("fal", "b", fine)
        await worker.drain()
        assert ok == ["done"]
        return worker

    worker = anyio.run(run)
    assert worker.stats.failed == 1 and worker.stats.completed == 1


def test_submit_threadsafe_from_a_handler_thread() -> None:
    async def run() -> list[str]:
        worker = WarmWorker()
        worker.bind_loop(asyncio.get_running_loop())
        done: list[str] = []

        async def job() -> None:
            done.append("voiceover")

        await anyio.to_thread.run_sync(worker.submit_threadsafe, "elevenlabs", "k", job)
        with anyio.fail_after(1):
            while not done:
                await asyncio.sleep(0.001)
        return done

    assert anyio.run(run) == ["voiceover"]


def test_closed_worker_rejects_and_cancels() -> None:
    async def run() -> WarmWorker:
        worker = WarmWorker()

        async def forever() -> None:
            await asyncio.sleep(3600)

        worker.submit("fal", "video", forever)
        await worker.aclose()
        assert not worker.submit("fal", "other", forever)
        return worker

    worker = anyio.run(run)
    assert worker.stats.rejected == 1
