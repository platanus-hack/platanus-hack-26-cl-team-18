from __future__ import annotations

import asyncio
import json

import pytest

from agent.speech.scribe import (
    MIN_CHUNK_BYTES,
    CommittedTranscript,
    ErrorClass,
    PartialTranscript,
    ScribeConfig,
    ScribeError,
    ScribeEvent,
    ScribeFatalError,
    ScribeRealtime,
    SessionStarted,
    parse_event,
)
from agent.speech.vad import FRAME_BYTES
from tests.fake_scribe import FakeScribe, wait_for

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


VOICE = b"\x01\x02" * (FRAME_BYTES // 2)


def config(url: str, key: str = "test-key") -> ScribeConfig:
    return ScribeConfig(api_key=key, url=url, keyterms=("hambre", "café"))


# ------------------------------------------------------------------ pure parts


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ({"message_type": "session_started", "session_id": "abc"}, SessionStarted("abc")),
        ({"message_type": "partial_transcript", "text": "uf qué"}, PartialTranscript("uf qué")),
        ({"message_type": "committed_transcript", "text": "uf"}, CommittedTranscript("uf")),
        (
            {
                "message_type": "committed_transcript_with_timestamps",
                "text": "uf",
                "language_code": "es",
                "words": [],
            },
            CommittedTranscript("uf", "es"),
        ),
    ],
)
def test_parse_known_events(raw: dict[str, object], expected: ScribeEvent) -> None:
    assert parse_event(json.dumps(raw)) == expected


@pytest.mark.parametrize(
    ("kind", "error_class"),
    [
        ("auth_error", ErrorClass.FATAL),
        ("quota_exceeded", ErrorClass.FATAL),
        ("unaccepted_terms", ErrorClass.FATAL),
        ("rate_limited", ErrorClass.TRANSIENT),
        ("session_time_limit_exceeded", ErrorClass.TRANSIENT),
        ("commit_throttled", ErrorClass.IGNORABLE),
        ("chunk_size_exceeded", ErrorClass.IGNORABLE),
        ("some_new_error", ErrorClass.TRANSIENT),
    ],
)
def test_errors_are_classified(kind: str, error_class: ErrorClass) -> None:
    event = parse_event(json.dumps({"message_type": kind, "error": "x"}))
    assert isinstance(event, ScribeError) and event.error_class is error_class


def test_unknown_and_malformed_frames() -> None:
    assert parse_event(json.dumps({"message_type": "warning", "warning": "slow"})) is None
    assert isinstance(parse_event("{nope"), ScribeError)


def test_config_url_carries_the_protocol_parameters_and_never_the_key() -> None:
    cfg = ScribeConfig(api_key="secret", keyterms=("hambre", "café"))
    url = cfg.connect_url()
    assert "model_id=scribe_v2_realtime" in url
    assert "audio_format=pcm_16000" in url and "commit_strategy=manual" in url
    assert "language_code=es" in url and url.count("keyterms=") == 2
    assert "secret" not in url and "secret" not in repr(cfg)


def test_config_enforces_scribe_keyterm_limits() -> None:
    with pytest.raises(ValueError):
        ScribeConfig(api_key="k", keyterms=tuple(f"t{i}" for i in range(51)))
    with pytest.raises(ValueError):
        ScribeConfig(api_key="")


# ------------------------------------------------------------------ against the fake server


async def test_streams_chunks_commits_and_delivers_transcripts_in_order() -> None:
    fake = FakeScribe(transcripts=["uf, qué hambre"])
    events: list[ScribeEvent] = []
    client = ScribeRealtime(config(await fake.start()), events.append)
    task = asyncio.create_task(client.run())
    try:
        await client.wait_connected(2)
        for _ in range(7):  # 7 × 32 ms
            client.send_audio(VOICE)
        client.commit()
        await wait_for(lambda: any(isinstance(e, CommittedTranscript) for e in events))
        texts = [type(e).__name__ for e in events if not isinstance(e, SessionStarted)]
        assert texts == ["PartialTranscript", "PartialTranscript", "CommittedTranscript"]
        assert fake.audio_bytes == 7 * FRAME_BYTES  # every sample arrived exactly once
        assert all(len(c["audio_base_64"]) > 0 for c in fake.chunks)
        assert [c["commit"] for c in fake.chunks][-1] is True
        assert fake.queries[0]["keyterms"] == ["hambre", "café"]
    finally:
        task.cancel()
        await fake.stop()


async def test_chunks_are_at_least_96ms() -> None:
    fake = FakeScribe()
    client = ScribeRealtime(config(await fake.start()), lambda e: None)
    task = asyncio.create_task(client.run())
    try:
        await client.wait_connected(2)
        for _ in range(9):
            client.send_audio(VOICE)
        await wait_for(lambda: fake.audio_bytes == 9 * FRAME_BYTES)
        sizes = {len(c["audio_base_64"]) for c in fake.chunks}
        assert len(sizes) == 1 and fake.chunks[0]["commit"] is False
        assert len(fake.chunks) == 3  # 3 chunks of 96 ms
        assert MIN_CHUNK_BYTES == 3 * FRAME_BYTES
    finally:
        task.cancel()
        await fake.stop()


async def test_sends_silence_keepalives_when_idle() -> None:
    fake = FakeScribe()
    client = ScribeRealtime(config(await fake.start()), lambda e: None, keepalive_s=0.03)
    task = asyncio.create_task(client.run())
    try:
        await client.wait_connected(2)
        await wait_for(lambda: fake.keepalives >= 3)
        assert client.stats.keepalives >= 3
    finally:
        task.cancel()
        await fake.stop()


async def test_transient_error_reconnects_and_keeps_transcribing() -> None:
    fake = FakeScribe(
        transcripts=["primero", "uf qué hambre"], error_after_commits={1: "rate_limited"}
    )
    events: list[ScribeEvent] = []
    client = ScribeRealtime(config(await fake.start()), events.append, backoff=(0.01,))
    task = asyncio.create_task(client.run())
    try:
        await client.wait_connected(2)
        client.send_audio(VOICE * 3)
        client.commit()  # -> rate_limited, server closes
        await wait_for(lambda: fake.sessions == 2 and client.connected)
        client.send_audio(VOICE * 3)
        client.commit()
        await wait_for(lambda: any(isinstance(e, CommittedTranscript) for e in events))
        assert client.stats.transient_errors == 1
        committed = [e for e in events if isinstance(e, CommittedTranscript)]
        assert committed[-1].text == "uf qué hambre"
    finally:
        task.cancel()
        await fake.stop()


async def test_fatal_auth_error_stops_and_reports() -> None:
    fake = FakeScribe()
    events: list[ScribeEvent] = []
    client = ScribeRealtime(config(await fake.start(), key="wrong"), events.append, backoff=(0.01,))
    try:
        with pytest.raises(ScribeFatalError, match="auth_error"):
            await asyncio.wait_for(client.run(), 3)
        assert client.fatal is not None and client.fatal.kind == "auth_error"
        assert any(isinstance(e, ScribeError) and e.error_class is ErrorClass.FATAL for e in events)
        client.send_audio(VOICE)  # no-op after a fatal error, never raises
        assert fake.sessions == 0
    finally:
        await fake.stop()


async def test_offline_audio_is_bounded() -> None:
    async def down(url: str, headers: dict[str, str]):  # type: ignore[no-untyped-def]
        raise OSError("no network")

    client = ScribeRealtime(
        ScribeConfig(api_key="k"),
        lambda e: None,
        connect=down,
        max_buffer_bytes=4 * MIN_CHUNK_BYTES,
    )
    for _ in range(30):
        client.send_audio(VOICE)
    assert len(client._outbox) <= 4
    assert client.stats.dropped_audio_bytes > 0
