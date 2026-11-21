from __future__ import annotations

import asyncio
import base64
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
            CommittedTranscript("uf", "es", supplemental=True),
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
        ("commit_throttled", ErrorClass.TRANSIENT),
        ("chunk_size_exceeded", ErrorClass.FATAL),
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
    assert "model_id=scribe_v3_live" in url
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
    client = ScribeRealtime(config(await fake.start()), events.append, idle_close_s=None)
    task = asyncio.create_task(client.run())
    try:
        await client.wait_connected(2)
        for _ in range(7):  # 7 × 32 ms
            client.send_audio(VOICE)
        client.commit()
        await wait_for(lambda: any(isinstance(e, CommittedTranscript) for e in events))
        texts = [type(e).__name__ for e in events if not isinstance(e, SessionStarted)]
        assert texts == ["PartialTranscript"] * 3 + ["CommittedTranscript"]
        assert fake.audio_bytes == 7 * FRAME_BYTES  # every sample arrived exactly once
        assert all(len(c["audio_base_64"]) > 0 for c in fake.chunks)
        assert [c["commit"] for c in fake.chunks][-1] is True
        assert fake.queries[0]["keyterms"] == ["hambre", "café"]
    finally:
        task.cancel()
        await fake.stop()


async def test_chunks_are_at_least_100ms() -> None:
    fake = FakeScribe()
    client = ScribeRealtime(config(await fake.start()), lambda e: None, idle_close_s=None)
    task = asyncio.create_task(client.run())
    try:
        await client.wait_connected(2)
        for _ in range(12):
            client.send_audio(VOICE)
        await wait_for(lambda: fake.audio_bytes == 12 * FRAME_BYTES)
        sizes = {len(c["audio_base_64"]) for c in fake.chunks}
        assert len(sizes) == 1 and fake.chunks[0]["commit"] is False
        assert len(fake.chunks) == 3  # 3 chunks of 128 ms
        assert MIN_CHUNK_BYTES == 4 * FRAME_BYTES
    finally:
        task.cancel()
        await fake.stop()


async def test_sends_silence_keepalives_when_idle() -> None:
    fake = FakeScribe()
    client = ScribeRealtime(
        config(await fake.start()), lambda e: None, keepalive_s=0.03, idle_close_s=None
    )
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
    client = ScribeRealtime(
        config(await fake.start()), events.append, backoff=(0.01,), idle_close_s=None
    )
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
    client = ScribeRealtime(
        config(await fake.start(), key="wrong"), events.append, backoff=(0.01,), idle_close_s=None
    )
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


# ------------------------------------------------------------------ lazy sessions (cost)


async def test_no_session_is_opened_until_there_is_speech() -> None:
    fake = FakeScribe()
    client = ScribeRealtime(config(await fake.start()), lambda e: None, keepalive_s=0.02)
    task = asyncio.create_task(client.run())
    try:
        await asyncio.sleep(0.2)
        assert fake.sessions == 0 and client.stats.sessions == 0  # silence costs nothing
        client.send_audio(VOICE * 4)
        await wait_for(lambda: fake.audio_bytes == 4 * FRAME_BYTES)
        assert fake.sessions == 1
    finally:
        task.cancel()
        await fake.stop()


async def test_idle_session_closes_and_reopens_on_the_next_speech() -> None:
    fake = FakeScribe(transcripts=["primero", "uf qué hambre"])
    events: list[ScribeEvent] = []
    client = ScribeRealtime(
        config(await fake.start()), events.append, keepalive_s=0.02, idle_close_s=0.1
    )
    task = asyncio.create_task(client.run())
    try:
        client.send_audio(VOICE * 3)
        client.commit()
        await wait_for(lambda: any(isinstance(e, CommittedTranscript) for e in events))
        await wait_for(lambda: client.stats.idle_closes == 1 and not client.connected)
        keepalives_at_close = fake.keepalives
        await asyncio.sleep(0.2)
        assert fake.keepalives == keepalives_at_close  # closed: nothing is sent or billed
        assert fake.sessions == 1

        client.send_audio(VOICE * 3)  # someone speaks again
        client.commit()
        await wait_for(
            lambda: (
                [e.text for e in events if isinstance(e, CommittedTranscript)][-1:]
                == ["uf qué hambre"]
            )
        )
        assert fake.sessions == 2
        voiced = [base64.b64decode(c["audio_base_64"]) for c in fake.chunks]
        assert (
            sum(len(pcm) for pcm in voiced if any(pcm)) == 6 * FRAME_BYTES
        )  # every voiced byte, once
    finally:
        task.cancel()
        await fake.stop()


def test_outbox_bound_counts_real_pcm_bytes_not_assumed_small_chunks():
    client = ScribeRealtime(ScribeConfig(api_key="k"), lambda _: None, max_buffer_bytes=2 * 32000)
    client.send_audio(VOICE * 500)
    # Large send_audio calls produce full-second chunks, not 96 ms frames.
    assert (
        sum(len(base64.b64decode(json.loads(c.message)["audio_base_64"])) for c in client._outbox)
        <= 64000
    )


def test_pcm16_rejects_half_a_sample():
    client = ScribeRealtime(ScribeConfig(api_key="k"), lambda _: None)
    with pytest.raises(ValueError, match="PCM16"):
        client.send_audio(b"x")


async def test_timestamp_enrichment_is_not_another_shopping_request():
    fake = FakeScribe(supplemental_commits=True)
    events = []
    client = ScribeRealtime(config(await fake.start()), events.append, idle_close_s=None)
    task = asyncio.create_task(client.run())
    try:
        await client.wait_connected(2)
        client.send_audio(VOICE * 4)
        client.commit()
        await wait_for(lambda: any(isinstance(e, CommittedTranscript) for e in events))
        await asyncio.sleep(0.02)
        assert len([e for e in events if isinstance(e, CommittedTranscript)]) == 1
    finally:
        task.cancel()
        await fake.stop()


async def test_lost_final_resets_session_instead_of_silently_stopping_transcription():
    from agent.speech.scribe import StreamReset

    fake = FakeScribe(withhold_commits={1})
    events = []
    client = ScribeRealtime(
        config(await fake.start()),
        events.append,
        idle_close_s=None,
        keepalive_s=0.01,
        response_timeout_s=0.05,
        backoff=(0.01,),
    )
    task = asyncio.create_task(client.run())
    try:
        await client.wait_connected(2)
        client.send_audio(VOICE * 4)
        client.commit()
        await wait_for(lambda: fake.sessions >= 2 and client.connected)
        assert any(isinstance(e, StreamReset) for e in events)
        client.send_audio(VOICE * 4)
        client.commit()
        await wait_for(lambda: any(isinstance(e, CommittedTranscript) for e in events))
    finally:
        task.cancel()
        await fake.stop()


async def test_stale_offline_audio_is_dropped_before_it_can_change_a_live_ad():
    from dataclasses import replace

    from agent.speech.scribe import StreamReset

    fake = FakeScribe()
    events = []
    client = ScribeRealtime(
        config(await fake.start()), events.append, idle_close_s=None, backoff=(0.01,)
    )
    client.send_audio(VOICE * 4)
    client.commit()
    client._outbox[0] = replace(client._outbox[0], queued_at=-100)
    task = asyncio.create_task(client.run())
    try:
        await wait_for(lambda: any(isinstance(e, StreamReset) for e in events))
        assert fake.audio_bytes == 0
        assert client.stats.dropped_audio_bytes >= 4 * FRAME_BYTES
    finally:
        task.cancel()
        await fake.stop()


def test_repeated_empty_commits_do_not_open_sessions_or_throttle_the_provider():
    client = ScribeRealtime(ScribeConfig(api_key="k"), lambda _: None)
    client.commit()
    assert not client._outbox
    client.send_audio(VOICE * 4)
    client.commit()
    count = len(client._outbox)
    client.commit()
    assert len(client._outbox) == count
    assert client.stats.commits == 1
