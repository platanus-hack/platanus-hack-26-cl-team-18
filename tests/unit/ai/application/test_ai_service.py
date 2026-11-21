from __future__ import annotations

import logging

import pytest
from pydantic import BaseModel

from app.modules.ai.application.errors import (
    MissingUserPromptError,
    OutputParseError,
    PromptBlockedError,
)
from app.modules.ai.application.service.llm import AIService
from app.modules.ai.domain.llm.enums import ProviderRole
from app.modules.ai.domain.llm.provider import ProviderMessage, ProviderResponse
from app.modules.ai.llm.guardrail.model import GuardrailResponse
from tests.unit.ai.async_runner import run_async


class FakeProvider:
    def __init__(self, outputs: list[str]) -> None:
        self.outputs = outputs
        self.calls = 0
        self.closed = False

    async def complete(self, messages, params=None) -> ProviderResponse:
        del messages, params
        self.calls += 1
        return ProviderResponse.create(self.outputs.pop(0))

    async def aclose(self) -> None:
        self.closed = True


class BlockGuardrail:
    async def validate_prompt(self, prompt: str) -> GuardrailResponse:
        del prompt
        return GuardrailResponse(is_valid=False, reason="blocked")


class RaiseGuardrail:
    async def validate_prompt(self, prompt: str) -> GuardrailResponse:
        del prompt
        raise RuntimeError("guardrail down")


class DemoSchema(BaseModel):
    ok: bool


def test_ai_service_blocks_invalid_prompt() -> None:
    provider = FakeProvider(outputs=["unused"])
    svc = AIService(provider=provider, guardrail=BlockGuardrail())

    with pytest.raises(PromptBlockedError):
        run_async(
            svc.structured_output(
                messages=[ProviderMessage.create(role=ProviderRole.USER, content="hi")],
            )
        )

    assert provider.calls == 0


def test_ai_service_guardrail_fail_open_option() -> None:
    provider = FakeProvider(outputs=["ok"])
    svc = AIService(
        provider=provider,
        guardrail=RaiseGuardrail(),
        fail_open_on_guardrail_error=True,
    )

    out = run_async(
        svc.generate(messages=[ProviderMessage.create(role=ProviderRole.USER, content="hi")])
    )

    assert out == "ok"
    assert provider.calls == 1


def test_ai_service_logs_structured_success(caplog) -> None:
    caplog.set_level(logging.INFO)
    provider = FakeProvider(outputs=["ok"])
    svc = AIService(provider=provider)

    out = run_async(
        svc.generate(messages=[ProviderMessage.create(role=ProviderRole.USER, content="hi")])
    )

    assert out == "ok"
    record = next(record for record in caplog.records if record.msg == "provider_call_succeeded")
    assert record.output_length == 2


def test_ai_service_missing_user_prompt_error() -> None:
    provider = FakeProvider(outputs=["ignored"])
    svc = AIService(provider=provider)

    with pytest.raises(MissingUserPromptError):
        run_async(
            svc.structured_output(
                messages=[ProviderMessage.create(role=ProviderRole.SYSTEM, content="rules")],
                schema=DemoSchema,
            )
        )


def test_ai_service_parser_errors_are_wrapped() -> None:
    provider = FakeProvider(outputs=['{"ok":false}'])
    svc = AIService(provider=provider)

    def bad_parser(s: str, schema=None, force_return: bool = False):
        del s, schema, force_return
        raise RuntimeError("parse fail")

    with pytest.raises(OutputParseError):
        run_async(
            svc.structured_output(
                messages=[ProviderMessage.create(role=ProviderRole.USER, content="hi")],
                parser=bad_parser,
                schema=DemoSchema,
            )
        )


def test_ai_service_aclose_delegates_to_provider() -> None:
    provider = FakeProvider(outputs=["ok"])
    svc = AIService(provider=provider)

    run_async(svc.aclose())

    assert provider.closed is True
