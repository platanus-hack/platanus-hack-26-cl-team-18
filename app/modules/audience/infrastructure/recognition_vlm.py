"""Warm, bounded enrichment of an already observed anonymous tracklet."""

from __future__ import annotations

import asyncio
import io
import threading
from typing import Literal, cast

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

from app.contract.messages import AudienceKeyframe, RecognitionAttribute
from app.modules.ai.application.service.llm import AIService
from app.modules.ai.domain.llm.enums import ProviderRole
from app.modules.ai.domain.llm.provider import InferenceParams, ProviderMessage
from app.modules.ai.infrastructure.parsers.pydantic_json import (
    parse_pydantic_json,
    structured_output_for,
)
from app.modules.audience.infrastructure.recognition_store import RecognitionStore
from app.realtime.router import InboundRouter, Received
from app.realtime.warm_worker import WarmWorker

SYSTEM = (
    "Describe only visible clothing, footwear, accessories and objects in this anonymized image. "
    "Do not infer identity, age, race, religion, health, orientation, politics or emotion. "
    "Report a brand only if a readable brand name or logo is visible; cite that evidence. "
    "If uncertain omit it. Do not infer brands from style. Uncertainty is qualitative, "
    "not a calibrated probability. Return concise descriptions and visible evidence."
)


def has_opaque_head_redaction(image: bytes) -> bool:
    """Reject malformed images and images lacking the selector's black upper band."""
    try:
        with Image.open(io.BytesIO(image)) as decoded:
            if decoded.format != "JPEG" or not (
                96 <= decoded.width <= 4096 and 160 <= decoded.height <= 4096
            ):
                return False
            top = decoded.crop((0, 0, decoded.width, int(decoded.height * 0.35))).convert("RGB")
            extrema = cast(tuple[tuple[int, int], ...], top.getextrema())
            return all(maximum <= 10 for _minimum, maximum in extrema)
    except Exception:
        return False


class VisualDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal[
        "upper_clothing", "lower_clothing", "footwear", "accessory", "brand", "object", "activity"
    ]
    value: str = Field(max_length=256)
    evidence: str = Field(min_length=1, max_length=500)
    associated_item: str = Field(max_length=128)
    uncertainty: Literal["low", "medium", "high", "unknown"]


class VisualDetails(BaseModel):
    model_config = ConfigDict(extra="forbid")
    details: list[VisualDetail] = Field(max_length=16)


class RecognitionVlm:
    def __init__(
        self,
        ai: AIService,
        model: str,
        worker: WarmWorker,
        store: RecognitionStore,
        router: InboundRouter,
    ):
        self.ai, self.model, self.worker, self.store = ai, model, worker, store
        self.router = router
        self.slots = threading.BoundedSemaphore(4)
        self.lock = threading.Lock()
        self.inflight: set[tuple[str, str, str]] = set()

    def accept(self, header: AudienceKeyframe, received: Received) -> None:
        if (
            not header.session_id
            or not header.track_id
            or header.video_time_s is None
            or received.payload is None
            or not self.store.contains_track(
                header.screen_id, header.session_id, header.track_id, header.video_time_s
            )
            or not has_opaque_head_redaction(received.payload)
        ):
            return
        session_id = header.session_id
        track_id = header.track_id
        video_time_s = header.video_time_s
        image = received.payload
        key = (header.screen_id, session_id, track_id)
        with self.lock:
            if key in self.inflight or not self.slots.acquire(blocking=False):
                return
            self.inflight.add(key)

        async def job() -> None:
            try:
                messages = [
                    ProviderMessage.create(ProviderRole.SYSTEM, SYSTEM),
                    ProviderMessage.create(
                        ProviderRole.USER, "Describe visible details.", image_jpeg=image
                    ),
                ]
                result: VisualDetails = await asyncio.wait_for(
                    self.ai.structured_output(
                        messages,
                        params=InferenceParams.create(
                            structured_output=structured_output_for(VisualDetails)
                        ),
                        schema=VisualDetails,
                        parser=parse_pydantic_json,
                        max_retries=1,
                    ),
                    timeout=20,
                )
                attributes = [
                    RecognitionAttribute(
                        name=detail.kind,
                        value=detail.value,
                        source="vlm",
                        observed_at_s=video_time_s,
                        evidence=detail.evidence,
                        uncertainty=detail.uncertainty,
                        model=self.model,
                        associated_item=detail.associated_item,
                        crop_ref=f"{session_id}:{track_id}:{header.seq}",
                    )
                    for detail in result.details
                    if detail.value.strip().lower() not in {"", "unknown"}
                ]
                enriched = bool(attributes) and self.store.enrich(
                    header.screen_id, session_id, track_id, video_time_s, attributes
                )
                if enriched and attributes:
                    item = self.store.get(header.screen_id, received.received_at)
                    if item is not None:
                        await self.router.dispatch(
                            item[0], Received(received_at=received.received_at)
                        )
            finally:
                with self.lock:
                    self.inflight.remove(key)
                    self.slots.release()

        self.worker.submit_threadsafe("vlm", f"{header.screen_id}:{session_id}:{track_id}", job)
