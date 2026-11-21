"""The shipped IAB catalog and the Claude adapters built from it."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.modules.ai.application.service.llm import AIService
from app.modules.ai.domain.llm.enums import ProviderRole
from app.modules.ai.domain.llm.provider import ProviderResponse
from app.modules.audience.domain.catalog import (
    CueRule,
    SegmentCatalog,
    SegmentTier,
    normalize_cue,
)
from app.modules.audience.domain.value_objects.segment import SegmentId, SegmentSource
from app.modules.audience.infrastructure.catalog.loader import load_catalog
from app.modules.audience.infrastructure.tagging.claude import ClaudeAudienceTagger
from app.modules.common.domain.value_objects.percentage import Percentage
from tests.unit.ai.async_runner import run_async

CATALOG = load_catalog().catalog


class RecordingProvider:
    """A fake ILLMProvider replaying one recorded structured response."""

    def __init__(self, output: dict) -> None:
        self.output = json.dumps(output)
        self.messages = []
        self.params = None

    async def complete(self, messages, params=None) -> ProviderResponse:
        self.messages = list(messages)
        self.params = params
        return ProviderResponse.create(self.output)

    async def aclose(self) -> None:
        return None


# ------------------------------------------------------------------ catalog


def test_catalog_is_interest_and_purchase_intent_only() -> None:
    assert len(CATALOG.segments) >= 20
    assert {segment.tier for segment in CATALOG.segments} <= {
        SegmentTier.INTEREST,
        SegmentTier.PURCHASE_INTENT,
    }
    assert not any(s.path.startswith("Demographic") for s in CATALOG.segments)
    assert CATALOG.label(SegmentId.create("977")) == "Coffee"


def test_every_cue_is_normalized_and_resolves() -> None:
    for rule in CATALOG.cues:
        assert rule.cue == normalize_cue(rule.cue)
        assert rule.segment_id in CATALOG
    assert [r.segment_id.value for r in CATALOG.rules_for(SegmentSource.VISION, " Hot  Dog")] == [
        "1404"
    ]


def test_brand_refs_are_the_prop_marker_slugs() -> None:
    markers = json.loads(
        (Path(__file__).parents[3] / "edge" / "props" / "markers.json").read_text(encoding="utf-8")
    )
    assert set(CATALOG.brand_refs) == set(markers.values())


def test_catalog_rejects_cues_outside_it_and_duplicate_ids() -> None:
    stray = CueRule(
        source=SegmentSource.VISION,
        cue="cup",
        segment_id=SegmentId.create("1"),
        weight=Percentage.create(50),
    )
    with pytest.raises(ValueError, match="outside the catalog"):
        CATALOG.with_cues((stray,))
    with pytest.raises(ValueError, match="unique"):
        SegmentCatalog(version="x", segments=CATALOG.segments[:1] * 2)


# ------------------------------------------------------------------ claude


def test_keyframe_schema_enumerates_the_catalog_and_has_no_person_fields() -> None:
    provider = RecordingProvider({})
    tagger = ClaudeAudienceTagger(
        ai=AIService(provider), catalog=CATALOG, model_version="claude-haiku-5-5@low"
    )
    schema = json.loads(tagger._params.structured_output.schema_json)
    defs = schema["$defs"]
    assert set(defs["KeyframeSegment"]["properties"]["iab_id"]["enum"]) == {
        s.segment_id.value for s in CATALOG.segments
    }
    fields = set(schema["properties"]) | {
        key for definition in defs.values() for key in definition["properties"]
    }
    assert not fields & {"age", "gender", "emotion", "ethnicity", "race", "identity"}
    assert "Never describe a person" in tagger.system_prompt


def test_tagger_sends_the_image_and_translates_the_answer() -> None:
    provider = RecordingProvider(
        {
            "segments": [
                {"iab_id": "544", "viewer_count": 9, "confidence": 1.7},
                {"iab_id": "977", "viewer_count": 1, "confidence": 0.6},
            ],
            "held_products": [{"category": "coffee", "brand_ref": "cafe-patio", "confidence": 0.9}],
            "minor_likely": False,
            "minor_likely_confidence": 0.1,
            "reasoning": "a   dog leash\nand a cup",
        }
    )
    tagger = ClaudeAudienceTagger(
        ai=AIService(provider), catalog=CATALOG, model_version="claude-haiku-5-5@low"
    )
    tags = run_async(tagger.tag(jpeg=b"\xff\xd8jpeg", looking=2))

    system, user = provider.messages
    assert system.role is ProviderRole.SYSTEM and system.content == tagger.system_prompt
    assert user.images[0].media_type == "image/jpeg" and user.images[0].data == b"\xff\xd8jpeg"
    assert "People looking at the screen: 2" in user.content
    dog, coffee = tags.segments
    assert (dog.segment_id.value, dog.viewer_count, dog.confidence) == ("544", 2, 1.0)
    assert coffee.confidence == 0.6
    assert tags.held_products[0].brand_ref == "cafe-patio"
    assert tags.reasoning == "a dog leash and a cup"
    assert tags.model_version == "claude-haiku-5-5@low"
