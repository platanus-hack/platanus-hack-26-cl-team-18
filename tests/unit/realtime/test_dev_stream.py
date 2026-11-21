"""`/dev` plumbing: hub routing, warm-job lifecycle events, prod gating, privacy shape."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

import anyio
import pytest

from app.bootstrap import dev_stream
from app.config.settings import AppSettings
from app.contract import messages
from app.contract.messages import ConsoleDegradation, DegradationFlag, DevJob
from app.realtime.hub import ConnectionHub
from app.realtime.warm_worker import GatedWarm, WarmEvent, WarmWorker

T0 = datetime(2026, 11, 21, 15, 0, tzinfo=UTC)


class _Sink:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_text(self, data: str) -> None:
        self.sent.append(data)

    def types(self) -> list[str]:
        return [json.loads(item)["type"] for item in self.sent]


def _job() -> DevJob:
    return DevJob(provider="claude", key="copy:demo-01:x", kind="copy", state="done", at=T0)


def test_devs_get_console_and_dev_messages_consoles_never_get_dev() -> None:
    async def run() -> tuple[_Sink, _Sink]:
        hub = ConnectionHub()
        console, dev = _Sink(), _Sink()
        hub.add_console(console)
        hub.add_dev(dev)
        await hub.broadcast_console(
            ConsoleDegradation(screen_id="demo-01", flags=[DegradationFlag.VLM_UNAVAILABLE])
        )
        await hub.broadcast_dev(_job())
        return console, dev

    console, dev = anyio.run(run)
    assert console.types() == ["console.degradation"]
    assert dev.types() == ["console.degradation", "dev.job"]


def test_dev_threadsafe_is_free_without_devs() -> None:
    hub = ConnectionHub()  # no loop bound: scheduling would count a drop
    hub.broadcast_dev_threadsafe(_job())
    assert hub.stats.dropped_no_loop == 0
    assert hub.dev_count() == 0


def test_removing_a_dev_retires_it() -> None:
    hub = ConnectionHub()
    sink = _Sink()
    hub.add_dev(sink)
    assert hub.dev_count() == 1
    hub.remove_dev(sink)
    assert hub.dev_count() == 0


def test_warm_worker_reports_each_transition_without_payloads() -> None:
    events: list[WarmEvent] = []

    async def run() -> None:
        worker = WarmWorker()
        worker.observe(events.append)
        gate = asyncio.Event()

        async def ok() -> None:
            await gate.wait()

        async def boom() -> None:
            raise ValueError("secret provider message")

        worker.submit("claude", "copy:demo-01:sig", ok)
        worker.submit("claude", "copy:demo-01:sig", ok)  # deduped while in flight
        worker.submit("vlm", "tag:demo-01", boom)
        await asyncio.sleep(0)
        gate.set()
        await worker.drain()
        GatedWarm(worker, lambda: False).submit_threadsafe("elevenlabs", "voice:demo-01:k", ok)

    anyio.run(run)
    states = [(e.provider, e.state) for e in events]
    assert ("claude", "submitted") in states
    assert ("claude", "deduped") in states
    assert ("claude", "running") in states
    assert ("claude", "done") in states
    assert ("vlm", "failed") in states
    assert ("elevenlabs", "rejected") in states
    failed = next(e for e in events if e.state == "failed")
    assert failed.error_type == "ValueError"  # the type only, never the message
    assert failed.elapsed_ms is not None
    done = next(e for e in events if e.state == "done")
    assert done.key == "copy:demo-01:sig"


def test_a_raising_observer_never_breaks_warm_work() -> None:
    ran = []

    def bad(_event: WarmEvent) -> None:
        raise RuntimeError("observer bug")

    async def run() -> None:
        worker = WarmWorker()
        worker.observe(bad)

        async def job() -> None:
            ran.append(True)

        assert worker.submit("claude", "copy:x", job)
        await worker.drain()

    anyio.run(run)
    assert ran == [True]


def test_job_keys_map_to_kind_and_screen() -> None:
    event = WarmEvent(provider="vlm", key="tag:demo-01", state="running", in_flight={"vlm": 1})
    row = dev_stream.job(event, at=T0)
    assert (row.kind, row.screen_id, row.load[0].in_flight) == ("tag", "demo-01", 1)
    other = dev_stream.job(WarmEvent(provider="fal", key="weird", state="done"), at=T0)
    assert (other.kind, other.screen_id) == ("other", None)


@pytest.mark.parametrize(
    ("env", "flag", "preview", "enabled", "preview_on"),
    [
        ("local", True, True, True, True),
        ("local", True, False, True, False),
        ("local", False, True, False, False),
        ("prod", True, True, False, False),
    ],
)
def test_dev_console_is_never_on_in_prod(
    env: str, flag: bool, preview: bool, enabled: bool, preview_on: bool
) -> None:
    settings = AppSettings.model_validate(
        {"app_env": env, "dev_console": flag, "dev_keyframe_preview": preview}
    )
    assert settings.dev_console_enabled is enabled
    assert settings.dev_keyframe_preview_enabled is preview_on


FORBIDDEN = {"text", "transcript", "utterance", "jpeg", "image", "embedding", "face", "audio"}


def test_dev_messages_carry_no_words_audio_or_identity() -> None:
    """Privacy guard (invariant 3): only opt-in, face-blurred demo previews carry pixels.

    `dev.keyframe` (DEV_KEYFRAME_PREVIEW) and the console's camera view
    (`dev.perception.preview_jpeg_base64`, DEMO_CAMERA_PREVIEW), both off in prod.
    """
    allowed = {("DevPerception", "preview_jpeg_base64")}
    dev_models = [
        model
        for name, model in vars(messages).items()
        if name.startswith("Dev") and isinstance(model, type) and hasattr(model, "model_fields")
    ]
    assert dev_models
    for model in dev_models:
        if model is messages.DevKeyframe:
            continue
        for field in model.model_fields:
            if (model.__name__, field) in allowed:
                continue
            assert not FORBIDDEN & set(field.split("_")), f"{model.__name__}.{field}"
