"""Claude Haiku topic extractor behind `TopicExtractor` (warm path, structured output).

The system prompt is frozen (cacheable). The user turn carries the utterance and the
segment catalog. Output is constrained to catalog keys and validated again by
`accepted`. Provider failures return no topics: speech is a bonus signal, never a
dependency. The utterance text is never logged.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from decimal import Decimal

from pydantic import BaseModel, Field

from app.modules.ai.domain.llm.provider import InferenceParams
from app.modules.ai.domain.llm.service import build_message
from app.modules.ai.domain.model_capabilities import ModelProfile
from app.modules.ai.infrastructure.parsers.pydantic_json import (
    parse_pydantic_json,
    structured_output_for,
)
from app.modules.ai.ports.provider import ILLMProvider
from app.modules.audience.application.ports.topics import ExtractedTopic, accepted

SYSTEM_PROMPT = (
    "You map one short, anonymous utterance overheard near a store screen to shopping "
    "interest segments. Use ONLY the segment keys given in `catalog`. Return a segment "
    "only when the utterance clearly expresses that interest or need (e.g. hunger, "
    "thirst, tiredness, wanting coffee). `intent` is 0..1: 0.9+ for an explicit want "
    "('qué hambre', 'necesito un café'), around 0.5 for a mention, omit otherwise. "
    "Never infer anything about the speaker (age, gender, health, identity, mood). "
    "Return an empty list when nothing applies."
)


class _Topic(BaseModel):
    segment: str = Field(max_length=32)
    intent: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)


class _TopicsOut(BaseModel):
    topics: list[_Topic] = Field(default_factory=list, max_length=4)


class ClaudeTopicExtractor:
    name = "claude"

    def __init__(self, provider: ILLMProvider, profile: ModelProfile) -> None:
        self._provider = provider
        self._profile = profile
        self._params = InferenceParams.create(
            max_tokens=256, structured_output=structured_output_for(_TopicsOut)
        )

    async def extract(
        self, text: str, *, language: str, catalog: Mapping[str, str]
    ) -> tuple[ExtractedTopic, ...]:
        user = json.dumps(
            {"utterance": text[:500], "language": language, "catalog": dict(catalog)},
            ensure_ascii=False,
            sort_keys=True,
        )
        try:
            response = await self._provider.complete(
                build_message(system_prompt=SYSTEM_PROMPT, user_prompt=user), self._params
            )
            out = parse_pydantic_json(response.output, _TopicsOut)
        except Exception:  # speech is a bonus signal: a failure means "no topics"
            return ()
        if not isinstance(out, _TopicsOut):
            return ()
        return accepted(
            tuple(
                ExtractedTopic(
                    segment_key=topic.segment.strip(),
                    intent=Decimal(str(topic.intent)).quantize(Decimal("0.01")),
                    confidence=Decimal(str(topic.confidence)).quantize(Decimal("0.01")),
                )
                for topic in out.topics
            ),
            catalog,
        )
