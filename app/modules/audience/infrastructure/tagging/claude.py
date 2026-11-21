"""Claude adapter for `AudienceTagger`: face-blurred keyframes to catalog tags.

It goes through `AIService` with constrained decoding and a frozen, cached
system prompt built once from the catalog. Model output is translated to
catalog IDs here (anti-corruption): unknown values are dropped, confidences
clamped to [0, 1], counts to the people looking.
"""

from __future__ import annotations

from typing import Any

from app.modules.ai.application.service.llm import AIService
from app.modules.ai.domain.llm.enums import ProviderRole
from app.modules.ai.domain.llm.provider import ImageContent, InferenceParams, ProviderMessage
from app.modules.ai.infrastructure.parsers.pydantic_json import (
    parse_pydantic_json,
    structured_output_for,
)
from app.modules.audience.application.ports import (
    KeyframeTags,
    TaggedProduct,
    TaggedSegment,
)
from app.modules.audience.domain.catalog import SegmentCatalog
from app.modules.audience.domain.value_objects.segment import SegmentId
from app.modules.audience.infrastructure.tagging.schemas import keyframe_schema

MAX_TOKENS = 2048
MAX_REASONING = 280

_PRIVACY_RULES = """\
Privacy rules (binding):
- Describe objects, products, activities and context only. Never describe a person.
- Never infer or mention identity, age, gender, race or ethnicity, religion, health,
  disability, sexual orientation, political views or emotions.
- The only person-related signal allowed is `minor_likely`: true only when a child or
  young teen is clearly present. It is used solely to block age-restricted ads.
- Faces in the image are blurred on purpose. Do not try to see through the blur."""


def _catalog_block(catalog: SegmentCatalog) -> str:
    lines = [f"- {s.segment_id.value}: {s.path}" for s in catalog.segments]
    return "Allowed IAB Audience Taxonomy 1.1 segments (id: path):\n" + "\n".join(lines)


def _clamp(value: float) -> float:
    return min(max(float(value), 0.0), 1.0)


def _reasoning(text: str) -> str:
    return " ".join(text.split())[:MAX_REASONING]


class ClaudeAudienceTagger:
    """`AudienceTagger` over Claude vision. Implements the port structurally."""

    def __init__(self, *, ai: AIService, catalog: SegmentCatalog, model_version: str) -> None:
        self._ai = ai
        self._catalog = catalog
        self._model_version = model_version
        self._schema = keyframe_schema(catalog)
        self._params = InferenceParams.create(
            max_tokens=MAX_TOKENS, structured_output=structured_output_for(self._schema)
        )
        products = ", ".join(
            f"{p.category}{f' ({p.brand_ref})' if p.brand_ref else ''}" for p in catalog.products
        )
        self.system_prompt = "\n\n".join(
            (
                "You tag the audience in front of a retail screen in a convenience store, "
                "from one camera keyframe, for a real-time ad exchange.",
                _catalog_block(catalog),
                "Tasks:\n"
                "1. segments: IAB segments the people looking at the screen are likely "
                "interested in right now, judged from what they carry, wear as gear, or are "
                "doing (a gym bag, a skateboard, a coffee cup, a laptop). For each, "
                "viewer_count = how many of the people looking it applies to, and "
                "confidence in [0, 1]. Omit anything below 0.3. Prefer few, specific "
                "segments over many vague ones.\n"
                "2. held_products: branded products someone is holding, read from the "
                f"label or packaging. Known products: {products}. Use brand_ref only when "
                "the brand is legible; otherwise null. Never guess a brand.\n"
                "3. minor_likely and minor_likely_confidence.\n"
                "4. reasoning: one short sentence about the objects and context that drove "
                "the tags. No description of people.",
                _PRIVACY_RULES,
            )
        )

    async def tag(self, *, jpeg: bytes, looking: int) -> KeyframeTags:
        messages = [
            ProviderMessage.create(role=ProviderRole.SYSTEM, content=self.system_prompt),
            ProviderMessage.create(
                role=ProviderRole.USER,
                content=f"Face-blurred keyframe. People looking at the screen: {looking}.",
                images=(ImageContent.create(media_type="image/jpeg", data=jpeg),),
            ),
        ]
        result: Any = await self._ai.structured_output(
            messages, params=self._params, schema=self._schema, parser=parse_pydantic_json
        )
        catalog = self._catalog
        return KeyframeTags(
            segments=tuple(
                TaggedSegment(
                    segment_id=SegmentId.create(item.iab_id),
                    viewer_count=min(max(int(item.viewer_count), 0), max(looking, 0)),
                    confidence=_clamp(item.confidence),
                )
                for item in result.segments
                if SegmentId.create(item.iab_id) in catalog
            ),
            held_products=tuple(
                TaggedProduct(
                    category=item.category,
                    brand_ref=item.brand_ref,
                    confidence=_clamp(item.confidence),
                )
                for item in result.held_products
                if item.category in catalog.categories
                and (item.brand_ref is None or item.brand_ref in catalog.brand_refs)
            ),
            minor_likely=bool(result.minor_likely),
            minor_likely_confidence=_clamp(result.minor_likely_confidence),
            reasoning=_reasoning(result.reasoning),
            model_version=self._model_version,
        )
