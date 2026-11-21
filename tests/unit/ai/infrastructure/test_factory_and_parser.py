from __future__ import annotations

import json

import pytest
from pydantic import BaseModel

from app.modules.ai.infrastructure.adapters.llm.anthropic import AnthropicProvider
from app.modules.ai.infrastructure.adapters.llm.factory import (
    LLMNotConfigured,
    build_llm_provider,
)
from app.modules.ai.infrastructure.parsers.pydantic_json import (
    parse_pydantic_json,
    strict_json_schema,
    structured_output_for,
)
from tests.unit.ai.async_runner import run_async


class Reading(BaseModel):
    label: str
    confidence: float | None = None


class Batch(BaseModel):
    readings: list[Reading]


def test_factory_requires_api_key() -> None:
    with pytest.raises(LLMNotConfigured):
        build_llm_provider({})


def test_factory_builds_haiku_at_low_effort_by_default() -> None:
    provider = build_llm_provider({"ANTHROPIC_API_KEY": "k"})
    assert isinstance(provider, AnthropicProvider)
    assert provider.profile.describe() == "claude-haiku-5-5@low"
    run_async(provider.aclose())


def test_factory_honours_model_and_effort() -> None:
    provider = build_llm_provider(
        {
            "ANTHROPIC_API_KEY": "k",
            "ANTHROPIC_MODEL": "claude-sonnet-5-5",
            "ANTHROPIC_EFFORT": "high",
        }
    )
    assert isinstance(provider, AnthropicProvider)
    assert provider.profile.describe() == "claude-sonnet-5-5@high"
    run_async(provider.aclose())


def test_factory_rejects_an_unknown_effort() -> None:
    with pytest.raises(ValueError):
        build_llm_provider({"ANTHROPIC_API_KEY": "k", "ANTHROPIC_EFFORT": "turbo"})


def test_parser_validates_fenced_json() -> None:
    parsed = parse_pydantic_json('```json\n{"label": "x", "confidence": 0.5}\n```', Reading)
    assert parsed == Reading(label="x", confidence=0.5)


def test_parser_force_return_gives_raw_data_on_invalid() -> None:
    assert parse_pydantic_json('{"nope": 1}', Reading, force_return=True) == {"nope": 1}
    with pytest.raises(ValueError):
        parse_pydantic_json('{"nope": 1}', Reading)


def test_strict_schema_requires_all_fields_and_forbids_extras() -> None:
    schema = strict_json_schema(Batch)
    reading = schema["$defs"]["Reading"]
    assert reading["required"] == ["label", "confidence"]
    assert reading["additionalProperties"] is False
    assert schema["additionalProperties"] is False
    assert json.loads(structured_output_for(Batch).schema_json) == schema
