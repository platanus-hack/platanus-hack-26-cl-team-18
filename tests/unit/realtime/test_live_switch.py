"""The "En vivo" switch: two independent axes, derived flags, idempotent sets."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from app.contract.messages import AgentRole, LiveSet, LiveStatus
from app.modules.common.infrastructure.clock.fixed_clock import FixedClock
from app.realtime.live_switch import LiveSwitchboard
from app.realtime.warm_worker import GatedWarm, WarmWorker

T0 = datetime(2026, 11, 21, 15, tzinfo=UTC)


def board(*, starts_live: bool = False, speech: tuple[str, ...] = ("demo-01",)) -> LiveSwitchboard:
    return LiveSwitchboard(clock=FixedClock(T0), starts_live=starts_live, speech_screens=speech)


def test_standby_by_default_senses_listens_and_generates_nothing() -> None:
    status = board().status("demo-01")
    assert (status.live, status.sensing, status.listening, status.ai_generation) == (
        False,
        False,
        False,
        False,
    )
    assert status.mic and status.speech_opt_in and status.revision == 0


def test_going_live_turns_everything_on_for_an_opted_in_screen() -> None:
    switch = board()
    status = switch.set(LiveSet(screen_id="demo-01", live=True))
    assert status.sensing and status.listening and status.ai_generation
    assert status.revision == 1 and status.changed_at == T0
    assert switch.sensing("demo-01") and switch.listening("demo-01")
    assert switch.ai_generation("demo-01")


def test_a_screen_without_speech_opt_in_never_listens() -> None:
    switch = board(speech=())
    status = switch.set(LiveSet(screen_id="demo-01", live=True, mic=True))
    assert status.sensing and not status.listening and not status.speech_opt_in


def test_mic_is_an_independent_axis_kept_across_live_toggles() -> None:
    switch = board()
    switch.set(LiveSet(screen_id="demo-01", live=True))
    muted = switch.set(LiveSet(screen_id="demo-01", mic=False))
    assert muted.live and muted.sensing and not muted.listening
    off = switch.set(LiveSet(screen_id="demo-01", live=False))
    assert not off.mic
    again = switch.set(LiveSet(screen_id="demo-01", live=True))
    assert again.sensing and not again.listening  # the operator's mute survives


def test_a_repeated_set_is_a_no_op_without_notifications() -> None:
    switch = board()
    seen: list[tuple[LiveStatus, LiveStatus]] = []
    switch.on_change(lambda before, after: seen.append((before, after)))
    first = switch.set(LiveSet(screen_id="demo-01", live=True))
    retry = switch.set(LiveSet(screen_id="demo-01", live=True))
    assert retry.revision == first.revision == 1
    assert len(seen) == 1
    before, after = seen[0]
    assert not before.sensing and after.sensing


def test_screens_are_independent_and_agents_are_reported() -> None:
    switch = board()
    switch.bind_agents(lambda screen: [AgentRole.SPEECH, AgentRole.VISION] if screen == "a" else [])
    switch.set(LiveSet(screen_id="a", live=True))
    assert switch.sensing("a") and not switch.sensing("b")
    assert switch.status("a").agents == [AgentRole.SPEECH, AgentRole.VISION]
    assert [s.screen_id for s in switch.statuses()] == ["a"]


def test_the_change_instant_comes_from_the_clock() -> None:
    clock = FixedClock(T0)
    switch = LiveSwitchboard(clock=clock, starts_live=True)
    clock.advance(timedelta(seconds=5))
    assert switch.set(LiveSet(screen_id="s", live=False)).changed_at == T0 + timedelta(seconds=5)


def test_gated_warm_refuses_jobs_in_standby_and_submits_when_live() -> None:
    async def scenario() -> tuple[int, int, list[str]]:
        worker = WarmWorker()
        worker.bind_loop(asyncio.get_running_loop())
        live = False
        gated = GatedWarm(worker, lambda: live)
        ran: list[str] = []

        def job(name: str):  # type: ignore[no-untyped-def]
            async def run() -> None:
                ran.append(name)

            return run

        gated.submit_threadsafe("claude", "copy:1", job("standby"))
        live = True
        gated.submit_threadsafe("claude", "copy:2", job("live"))
        await asyncio.sleep(0)
        await worker.drain()
        return gated.refused, worker.stats.submitted, ran

    refused, submitted, ran = asyncio.run(scenario())
    assert (refused, submitted, ran) == (1, 1, ["live"])
