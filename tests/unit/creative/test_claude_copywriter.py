"""ClaudeCopywriter against a fake provider: payload, parsing, typed failures. No network."""

from __future__ import annotations

import json

import anyio
import pytest

from app.modules.ai.domain.llm.enums import ProviderRole
from app.modules.ai.domain.llm.provider import InferenceParams, ProviderMessage, ProviderResponse
from app.modules.ai.domain.model_capabilities import ModelProfile
from app.modules.ai.ports.errors import (
    ProviderCreditExhausted,
    ProviderRateLimited,
    ProviderUnavailable,
)
from app.modules.creative.application.ports.copywriter import CopyFailure, CopyWriterError
from app.modules.creative.domain.angle import Angle
from app.modules.creative.domain.draft import CopySource
from app.modules.creative.infrastructure.claude_copywriter import SYSTEM_PROMPT, ClaudeCopywriter
from tests.unit.creative.factory import brief

GROUP = brief(party=4, angle=Angle.HUNGER)
REPLY = {
    "teaser": "¿Bajón los 4? Voltio + Café Patio",
    "offer": "Para los 4 · Pack x4 post-entreno: Voltio + Café Patio por $10.970",
    "close": "Muestra el QR en caja · válido 10 min",
    "voice_line": "Para los 4, Voltio y Café Patio",
    "confidence": 0.93,
    "reasoning": "Hunger angle, group of four.",
}


class FakeProvider:
    def __init__(self, output: str = json.dumps(REPLY), error: Exception | None = None) -> None:
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


def write(provider: FakeProvider):  # type: ignore[no-untyped-def]
    writer = ClaudeCopywriter(provider, ModelProfile("claude-haiku-5-5", "low"))
    return anyio.run(writer.write, GROUP)


def test_parses_structured_reply_with_provenance() -> None:
    provider = FakeProvider()
    written = write(provider)
    draft = written.draft
    assert draft.source is CopySource.AI and draft.model_version == "claude-haiku-5-5@low"
    assert draft.teaser == REPLY["teaser"] and str(draft.confidence) == "0.93"
    assert written.usage.input_tokens > 0 and written.usage.output_tokens > 0


def test_system_prompt_is_frozen_and_the_brief_is_in_the_user_turn() -> None:
    provider = FakeProvider()
    write(provider)
    system = [m for m in provider.messages if m.role is ProviderRole.SYSTEM]
    user = [m for m in provider.messages if m.role is ProviderRole.USER]
    assert [m.content for m in system] == [SYSTEM_PROMPT]
    payload = json.loads(user[-1].content)
    assert payload["party"] == 4 and payload["angle"] == "hunger"
    assert payload["offer"]["price"] == "$10.970"
    assert "Rayo" in payload["avoid"] and "Voltio" not in payload["avoid"]
    assert "hambre" in payload["angle_words"]


@pytest.mark.parametrize(
    ("error", "failure"),
    [
        (ProviderCreditExhausted("x"), CopyFailure.QUOTA),
        (ProviderRateLimited("x"), CopyFailure.RATE_LIMITED),
        (ProviderUnavailable("x"), CopyFailure.UNAVAILABLE),
    ],
)
def test_provider_errors_are_typed(error: Exception, failure: CopyFailure) -> None:
    with pytest.raises(CopyWriterError) as caught:
        write(FakeProvider(error=error))
    assert caught.value.failure is failure


def test_unparsable_output_is_invalid() -> None:
    with pytest.raises(CopyWriterError) as caught:
        write(FakeProvider(output="lo siento, no puedo"))
    assert caught.value.failure is CopyFailure.INVALID_OUTPUT
