"""Claude keyframe tagger (warm path): objects and held branded products, never people."""

from __future__ import annotations

import json
from collections.abc import Sequence
from decimal import Decimal

from pydantic import BaseModel, Field

from app.modules.ai.domain.llm.enums import ProviderRole
from app.modules.ai.domain.llm.provider import InferenceParams, ProviderMessage
from app.modules.ai.domain.model_capabilities import ModelProfile
from app.modules.ai.infrastructure.parsers.pydantic_json import (
    parse_pydantic_json,
    structured_output_for,
)
from app.modules.ai.ports.provider import ILLMProvider
from app.modules.audience.application.ports.tagger import FrameTags, accepted_tags

SYSTEM_PROMPT = (
    "You see one face-blurred frame from a store screen's camera. Report ONLY: (1) which "
    "of the given object labels are visible, and (2) which of the given product brands "
    "someone is visibly holding, judged ONLY from a readable logo, label or the brand's "
    "printed colors on the product itself. Never describe people: no clothing, body, "
    "face, age, gender or appearance. Never guess a brand from style. Confidence is "
    "0..1; omit anything uncertain. `evidence` is a few words about the object, never "
    "about a person."
)


class _Seen(BaseModel):
    label: str = Field(max_length=64)
    confidence: float = Field(ge=0, le=1)
    evidence: str = Field(default="", max_length=120)


class _TagsOut(BaseModel):
    objects: list[_Seen] = Field(default_factory=list, max_length=12)
    held_brands: list[_Seen] = Field(default_factory=list, max_length=6)


def _decimal(value: float) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"))


class ClaudeKeyframeTagger:
    name = "claude"

    def __init__(self, provider: ILLMProvider, profile: ModelProfile) -> None:
        self._provider = provider
        self._profile = profile
        self._params = InferenceParams.create(
            max_tokens=400, structured_output=structured_output_for(_TagsOut)
        )

    async def tag(
        self, jpeg: bytes, *, object_labels: Sequence[str], brand_names: Sequence[str]
    ) -> FrameTags:
        user = json.dumps(
            {"object_labels": list(object_labels), "brands": list(brand_names)},
            ensure_ascii=False,
        )
        messages = [
            ProviderMessage.create(ProviderRole.SYSTEM, SYSTEM_PROMPT),
            ProviderMessage.create(ProviderRole.USER, user, image_jpeg=jpeg),
        ]
        try:
            response = await self._provider.complete(messages, self._params)
            out = parse_pydantic_json(response.output, _TagsOut)
        except Exception:  # warm path: a failure means "no new tags"
            return FrameTags()
        if not isinstance(out, _TagsOut):
            return FrameTags()
        tags = FrameTags(
            objects=tuple((seen.label.strip(), _decimal(seen.confidence)) for seen in out.objects),
            held_brands=tuple(
                (seen.label.strip(), _decimal(seen.confidence)) for seen in out.held_brands
            ),
            model_version=self._profile.describe(),
            reasoning=tuple(seen.evidence[:120] for seen in (*out.objects, *out.held_brands)),
        )
        return accepted_tags(tags, object_labels=object_labels, brand_names=brand_names)
