"""Synthetic model-response replay through the real speech -> auction -> player path.

Proves application behavior and adapter validation, not live model accuracy or WER.
All fixture utterances are authored; no live microphone data is collected here.
"""

from __future__ import annotations

import json
import tempfile
import threading
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from app.bootstrap.container import build_container
from app.config.settings import AppSettings
from app.contract.messages import AudienceSnapshot, AudienceUtterance, CreativeReady, MinorLikely
from app.main import create_app
from app.modules.ai.domain.llm.enums import ProviderRole
from app.modules.ai.domain.llm.provider import InferenceParams, ProviderMessage, ProviderResponse
from app.modules.ai.domain.model_capabilities import ModelProfile
from app.modules.auction.infrastructure.auction_log import InMemoryAuctionLog
from app.modules.audience.domain.value_objects.segment import SegmentSource
from app.modules.audience.infrastructure.claude_topics import ClaudeTopicExtractor
from app.modules.common.infrastructure.clock.fixed_clock import FixedClock

FIXTURE = Path(__file__).parent / "fixtures" / "speech" / "understanding.json"
T0 = datetime(2026, 11, 21, 12, tzinfo=UTC)


class ReplayProvider:
    """An explicit provider override; no network or credential lookup."""

    def __init__(self, response: str) -> None:
        self.response = response
        self.called = threading.Event()
        self.previous_topics: tuple[str, ...] = ()

    async def complete(
        self, messages: Sequence[ProviderMessage], params: InferenceParams | None = None
    ) -> ProviderResponse:
        user = next(m for m in messages if m.role is ProviderRole.USER)
        self.previous_topics = tuple(json.loads(user.content).get("previous_topics", ()))
        self.called.set()
        return ProviderResponse.create(self.response)

    async def aclose(self) -> None:
        pass


def run_case(case: dict[str, Any]) -> dict[str, Any]:
    provider = ReplayProvider(case.get("raw_response", json.dumps(case.get("response", {}))))
    extractor = ClaudeTopicExtractor(provider, ModelProfile("claude-haiku-5-5", "low"))
    settings_options: dict[str, Any] = {"_env_file": None}
    with tempfile.TemporaryDirectory(prefix="speech-understanding-") as tmp:
        container = build_container(
            AppSettings(
                **settings_options,
                app_env="test",
                speech_mode_screens=["demo-01"],
                media_dir=Path(tmp),
                anthropic_api_key=None,
                elevenlabs_api_key=None,
                fal_key=None,
                voiceovers_enabled=False,
                vlm_enabled=False,
            ),
            clock=FixedClock(T0),
            topic_extractor=extractor,
        )
        with (
            TestClient(create_app(container=container)) as client,
            client.websocket_connect("/ws/screen/demo-01") as player,
            client.websocket_connect("/ws/agent") as agent,
        ):
            player.receive_json()
            agent.send_text(
                AudienceSnapshot(
                    screen_id="demo-01",
                    seq=1,
                    measured_at=T0,
                    people=1,
                    looking=1,
                    minor_likely=MinorLikely(value=False, confidence=1),
                ).model_dump_json()
            )
            agent.send_text(
                AudienceUtterance(
                    screen_id="demo-01",
                    seq=1,
                    measured_at=T0,
                    text="",
                    language="es",
                    keyterms_hit=["cerveza"],
                    utterance_id="fixture:initial",
                ).model_dump_json()
            )
            assert (
                CreativeReady.model_validate(player.receive_json()).spec.slots[0].brand_name
                == "Malta Sur"
            )
            agent.send_text(
                AudienceUtterance(
                    screen_id="demo-01",
                    seq=2,
                    measured_at=T0,
                    text=case["text"],
                    language=case.get("language", "es"),
                    utterance_id="fixture:revision",
                ).model_dump_json()
            )
            if not provider.called.wait(2):
                raise AssertionError(f"semantic provider was not called: {case['id']}")
            assert client.portal is not None
            client.portal.call(container.warm.drain)
            with client.websocket_connect("/ws/screen/demo-01") as reconnected:
                shown = CreativeReady.model_validate(reconnected.receive_json())
            brands = (
                sorted(s.brand_name for s in shown.spec.slots if s.brand_id != "house")
                if shown.auction_id
                else []
            )
            assert isinstance(container.auction_log, InMemoryAuctionLog)
            moment = container.auction_log.records()[-1].moment
            topics = sorted(
                s.segment_id.value for s in moment.segments if SegmentSource.SPEECH in s.sources
            )
            return {
                "id": case["id"],
                "passed": brands == sorted(case["brands"])
                and topics == sorted(case["topics"])
                and provider.previous_topics == ("369",),
                "brands": brands,
                "topics": topics,
                "prior_topics": provider.previous_topics,
            }


def run_semantic_cases() -> list[dict[str, Any]]:
    return [run_case(case) for case in json.loads(FIXTURE.read_text())]
