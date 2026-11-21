"""A slow topic model must never resurrect an older request over an explicit new one."""

import asyncio
import threading
from datetime import timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from scripts.replay_audience import paid_brands, snapshot_for

from app.bootstrap.container import build_container
from app.config.settings import AppSettings
from app.contract.messages import AudienceUtterance, CreativeReady
from app.main import create_app
from app.modules.audience.application.ports.topics import ExtractedTopic, TopicExtraction
from app.modules.common.infrastructure.clock.fixed_clock import FixedClock
from tests.unit.api.test_speech_and_voice_flow import T0


class GatedTopics:
    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()

    async def extract(self, text, *, language, catalog, prior_topics=()):
        self.started.set()
        while not self.release.is_set():
            await asyncio.sleep(0.001)
        return TopicExtraction(
            topics=(
                ExtractedTopic(
                    segment_key="369", intent=Decimal("0.9"), confidence=Decimal("0.95")
                ),
            )
        )


class RestartedTopics(GatedTopics):
    def __init__(self):
        super().__init__()
        self.new_started = threading.Event()

    async def extract(self, text, *, language, catalog, prior_topics=()):
        if text == "new-request":
            self.new_started.set()
            return TopicExtraction(
                topics=(
                    ExtractedTopic(
                        segment_key="977", intent=Decimal("0.9"), confidence=Decimal("0.95")
                    ),
                )
            )
        return await super().extract(
            text, language=language, catalog=catalog, prior_topics=prior_topics
        )


def test_restarted_speech_sequence_does_not_dedupe_away_the_new_request(tmp_path):
    topics = RestartedTopics()
    clock = FixedClock(T0)
    container = build_container(
        AppSettings(
            _env_file=None, app_env="test", speech_mode_screens=["demo-01"], media_dir=tmp_path
        ),
        clock=clock,
        topic_extractor=topics,
    )
    with (
        TestClient(create_app(container=container)) as client,
        client.websocket_connect("/ws/screen/demo-01") as player,
        client.websocket_connect("/ws/agent") as old_agent,
    ):
        player.receive_json()
        old_agent.send_text(snapshot_for("fitness", 1, clock.now_utc()).model_dump_json())
        player.receive_json()
        old_agent.send_text(
            AudienceUtterance(
                screen_id="demo-01",
                seq=1,
                measured_at=clock.now_utc(),
                text="old-request",
                language="es",
            ).model_dump_json()
        )
        try:
            assert topics.started.wait(2)
            with client.websocket_connect("/ws/agent") as restarted:
                restarted.send_text(
                    AudienceUtterance(
                        screen_id="demo-01",
                        seq=1,
                        measured_at=clock.now_utc(),
                        text="new-request",
                        language="es",
                    ).model_dump_json()
                )
                assert topics.new_started.wait(2), (
                    "new agent sequence collided with the previous warm job"
                )
                assert set(paid_brands(CreativeReady.model_validate(player.receive_json()))) == {
                    "Voltio",
                    "Café Patio",
                }
        finally:
            topics.release.set()
        client.portal.call(container.warm.drain)


@pytest.mark.parametrize("new_request", [True, False])
def test_old_or_expired_model_result_cannot_replace_the_current_ad(tmp_path, new_request):
    topics = GatedTopics()
    clock = FixedClock(T0)
    settings = AppSettings(
        _env_file=None, app_env="test", speech_mode_screens=["demo-01"], media_dir=tmp_path
    )
    container = build_container(settings, clock=clock, topic_extractor=topics)
    with (
        TestClient(create_app(container=container)) as client,
        client.websocket_connect("/ws/screen/demo-01") as player,
        client.websocket_connect("/ws/agent") as agent,
    ):
        player.receive_json()
        agent.send_text(snapshot_for("fitness", 1, clock.now_utc()).model_dump_json())
        assert paid_brands(CreativeReady.model_validate(player.receive_json())) == ("Voltio",)
        agent.send_text(
            AudienceUtterance(
                screen_id="demo-01",
                seq=1,
                measured_at=clock.now_utc(),
                text="synthetic unknown request",
                language="es",
            ).model_dump_json()
        )
        try:
            assert topics.started.wait(2)
            if new_request:
                # Equal clock instants are deliberate: a revision, not timestamp ties,
                # must order model completion against a newer hot keyword event.
                agent.send_text(
                    AudienceUtterance(
                        screen_id="demo-01",
                        seq=2,
                        measured_at=clock.now_utc(),
                        text="",
                        language="es",
                        keyterms_hit=["café"],
                    ).model_dump_json()
                )
                expected = {"Voltio", "Café Patio"}
                assert (
                    set(paid_brands(CreativeReady.model_validate(player.receive_json())))
                    == expected
                )
            else:
                clock.advance(timedelta(seconds=61))
                expected = {"Voltio"}
        finally:
            topics.release.set()
        client.portal.call(container.warm.drain)
        with client.websocket_connect("/ws/screen/demo-01") as reconnect:
            assert (
                set(paid_brands(CreativeReady.model_validate(reconnect.receive_json()))) == expected
            )
