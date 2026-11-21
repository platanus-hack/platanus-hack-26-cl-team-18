"""AnthropicProvider against a fake SDK client (recorded response shapes, no network)."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from app.modules.ai.domain.errors import InvalidInferenceParams
from app.modules.ai.domain.llm.enums import ProviderRole
from app.modules.ai.domain.llm.provider import (
    InferenceParams,
    ProviderMessage,
    StructuredOutput,
)
from app.modules.ai.infrastructure.adapters.llm.anthropic import AnthropicProvider
from app.modules.ai.ports.errors import ProviderOutputIncomplete
from tests.unit.ai.async_runner import run_async

SCHEMA = '{"additionalProperties":false,"properties":{"segments":{"items":{"type":"string"},"type":"array"}},"required":["segments"],"type":"object"}'


class FakeMessages:
    def __init__(self, response: Any = None, error: BaseException | None = None) -> None:
        self.response = response
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response


class FakeClient:
    def __init__(self, response: Any = None, error: BaseException | None = None) -> None:
        self.messages = FakeMessages(response, error)
        self.closed = False

    async def close(self) -> None:
        self.closed = True


def reply(*blocks: Any, stop_reason: str = "end_turn") -> SimpleNamespace:
    return SimpleNamespace(id="msg_test", stop_reason=stop_reason, content=list(blocks))


def text(value: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=value)


def provider_with(client: FakeClient, **kwargs: Any) -> AnthropicProvider:
    return AnthropicProvider(client=client, **kwargs)  # type: ignore[arg-type]


def conversation() -> list[ProviderMessage]:
    return [
        ProviderMessage.create(role=ProviderRole.SYSTEM, content="Tag the moment with IAB ids."),
        ProviderMessage.create(role=ProviderRole.USER, content="2 adults, morning, gas station"),
    ]


def structured(max_tokens: int = 256) -> InferenceParams:
    return InferenceParams.create(
        max_tokens=max_tokens,
        structured_output=StructuredOutput.create(name="MomentTags", schema_json=SCHEMA),
    )


def test_request_defaults_to_haiku_at_low_effort_with_a_cached_system_prompt() -> None:
    client = FakeClient(reply(text("ok")))
    provider = provider_with(client)

    run_async(provider.complete(conversation()))

    [call] = client.messages.calls
    assert call["model"] == "claude-haiku-5-5"
    assert call["output_config"] == {"effort": "low"}
    assert call["system"] == [
        {
            "type": "text",
            "text": "Tag the moment with IAB ids.",
            "cache_control": {"type": "ephemeral"},
        }
    ]
    assert call["messages"] == [{"role": "user", "content": "2 adults, morning, gas station"}]
    assert "temperature" not in call and "top_p" not in call
    assert provider.profile.describe() == "claude-haiku-5-5@low"


def test_structured_output_constrains_decoding_to_the_schema() -> None:
    client = FakeClient(reply(text('{"segments":["IAB8"]}')))
    provider = provider_with(client)

    response = run_async(provider.complete(conversation(), structured(max_tokens=321)))

    [call] = client.messages.calls
    assert call["max_tokens"] == 321
    assert call["output_config"]["format"] == {
        "type": "json_schema",
        "schema": json.loads(SCHEMA),
    }
    assert json.loads(response.output) == {"segments": ["IAB8"]}


def test_free_text_calls_are_unconstrained_and_omit_an_empty_system_prompt() -> None:
    client = FakeClient(reply(text("Trío café + medialuna")))
    provider = provider_with(client)

    response = run_async(
        provider.complete([ProviderMessage.create(role=ProviderRole.USER, content="headline")])
    )

    [call] = client.messages.calls
    assert "format" not in call["output_config"]
    assert "system" not in call
    assert response.output == "Trío café + medialuna"


def test_thinking_blocks_are_skipped_and_text_blocks_joined() -> None:
    thinking = SimpleNamespace(type="thinking", thinking="")
    client = FakeClient(reply(thinking, text('{"segments":'), text('["IAB8"]}')))

    response = run_async(provider_with(client).complete(conversation(), structured()))

    assert response.output == '{"segments":["IAB8"]}'


@pytest.mark.parametrize("stop_reason", ["max_tokens", "refusal"])
def test_a_truncated_or_refused_answer_is_never_returned(stop_reason: str) -> None:
    client = FakeClient(reply(text('{"segments":["IAB'), stop_reason=stop_reason))

    with pytest.raises(ProviderOutputIncomplete) as raised:
        run_async(provider_with(client).complete(conversation(), structured()))

    assert raised.value.reason == stop_reason
    assert "IAB" not in str(raised.value)
    assert len(client.messages.calls) == 1


def test_a_structured_call_without_text_is_incomplete() -> None:
    client = FakeClient(reply(SimpleNamespace(type="thinking", thinking="")))

    with pytest.raises(ProviderOutputIncomplete) as raised:
        run_async(provider_with(client).complete(conversation(), structured()))

    assert raised.value.reason == "missing_output"


def test_aclose_closes_the_sdk_client() -> None:
    client = FakeClient()

    run_async(provider_with(client).aclose())

    assert client.closed


def test_inference_params_validate_max_tokens() -> None:
    with pytest.raises(InvalidInferenceParams):
        InferenceParams.create(max_tokens=0)
    with pytest.raises(InvalidInferenceParams):
        InferenceParams.create(max_tokens=True)  # type: ignore[arg-type]
