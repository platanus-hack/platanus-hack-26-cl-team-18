"""Speech topics for utterances the keyterm matcher missed (warm path)."""

import json
from decimal import Decimal

import anyio

from app.modules.ai.domain.llm.enums import ProviderRole
from app.modules.ai.domain.llm.provider import InferenceParams, ProviderMessage, ProviderResponse
from app.modules.ai.domain.model_capabilities import ModelProfile
from app.modules.audience.application.ports.topics import ExtractedTopic, accepted
from app.modules.audience.infrastructure.claude_topics import SYSTEM_PROMPT, ClaudeTopicExtractor

CATALOG = {"DEMO_FOOD": "Comida / hambre", "DEMO_COFFEE": "Café"}


class FakeProvider:
    def __init__(self, output: str = "", error: Exception | None = None) -> None:
        self.output, self.error = output, error
        self.messages: list[ProviderMessage] = []

    async def complete(
        self, messages: list[ProviderMessage], params: InferenceParams | None = None
    ) -> ProviderResponse:
        self.messages = messages
        assert params is not None and params.structured_output is not None
        if self.error is not None:
            raise self.error
        return ProviderResponse.create(self.output)

    async def aclose(self) -> None:
        return None


def extract(provider: FakeProvider, text: str = "me muero de hambre") -> tuple[ExtractedTopic, ...]:
    extractor = ClaudeTopicExtractor(provider, ModelProfile("claude-haiku-5-5", "low"))

    async def run() -> tuple[ExtractedTopic, ...]:
        return await extractor.extract(text, language="es", catalog=CATALOG)

    return anyio.run(run)


def test_only_known_confident_segments_come_back_strongest_first() -> None:
    topics = (
        ExtractedTopic(segment_key="DEMO_COFFEE", intent=Decimal("0.5"), confidence=Decimal("0.9")),
        ExtractedTopic(segment_key="DEMO_FOOD", intent=Decimal("0.9"), confidence=Decimal("0.95")),
        ExtractedTopic(segment_key="DEMO_FOOD", intent=Decimal("0.4"), confidence=Decimal("0.95")),
        ExtractedTopic(segment_key="POLITICS", intent=Decimal("1"), confidence=Decimal("1")),
        ExtractedTopic(segment_key="DEMO_COFFEE", intent=Decimal("0.9"), confidence=Decimal("0.3")),
    )
    assert [(t.segment_key, t.intent) for t in accepted(topics, CATALOG)] == [
        ("DEMO_FOOD", Decimal("0.9")),
        ("DEMO_COFFEE", Decimal("0.5")),
    ]


def test_claude_maps_an_utterance_to_catalog_segments_only() -> None:
    provider = FakeProvider(
        json.dumps(
            {
                "topics": [
                    {"segment": "DEMO_FOOD", "intent": 0.92, "confidence": 0.9},
                    {"segment": "MADE_UP", "intent": 0.9, "confidence": 0.9},
                ]
            }
        )
    )
    topics = extract(provider)
    assert [(t.segment_key, t.intent) for t in topics] == [("DEMO_FOOD", Decimal("0.92"))]
    system = [m for m in provider.messages if m.role is ProviderRole.SYSTEM]
    assert (
        system[0].content == SYSTEM_PROMPT
        and "Never infer anything about the speaker" in SYSTEM_PROMPT
    )


def test_a_provider_failure_or_garbage_means_no_topics() -> None:
    assert extract(FakeProvider(error=RuntimeError("down"))) == ()
    assert extract(FakeProvider("not json")) == ()
    assert extract(FakeProvider(json.dumps({"topics": []}))) == ()
