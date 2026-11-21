from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Self

from app.modules.ai.domain.errors import (
    InvalidInferenceParams,
    InvalidProviderMessageContent,
    InvalidProviderMessageRole,
    InvalidProviderResponseOutput,
)
from app.modules.ai.domain.llm.enums import ProviderRole
from app.modules.common.domain.primitives.value_object import ValueObject

ImageMediaType = Literal["image/jpeg", "image/png", "image/webp", "image/gif"]
_IMAGE_MEDIA_TYPES = frozenset({"image/jpeg", "image/png", "image/webp", "image/gif"})


@dataclass(frozen=True, slots=True)
class ImageContent(ValueObject):
    """An image sent with a user message. Bytes stay in memory: never log them."""

    media_type: ImageMediaType
    data: bytes

    @classmethod
    def create(cls, *, media_type: ImageMediaType, data: bytes) -> Self:
        image = cls(media_type=media_type, data=data)
        image._enforce_types()
        return image

    def _enforce_types(self) -> None:
        if self.media_type not in _IMAGE_MEDIA_TYPES:
            raise InvalidProviderMessageContent()
        if not isinstance(self.data, bytes) or not self.data:
            raise InvalidProviderMessageContent()

    def __repr__(self) -> str:  # keep image bytes out of logs and tracebacks
        return f"ImageContent(media_type={self.media_type!r}, bytes={len(self.data)})"


@dataclass(frozen=True, slots=True)
class ProviderMessage(ValueObject):
    role: ProviderRole
    content: str
    images: tuple[ImageContent, ...] = ()

    @classmethod
    def create(
        cls,
        role: ProviderRole | str,
        content: str,
        images: tuple[ImageContent, ...] = (),
    ) -> Self:
        try:
            normalized_role = ProviderRole(role)
        except Exception as exc:
            raise InvalidProviderMessageRole(role=role) from exc

        msg = cls(role=normalized_role, content=content, images=images)
        msg._enforce_types()
        return msg

    def _enforce_types(self) -> None:
        if not isinstance(self.role, ProviderRole):
            raise InvalidProviderMessageRole(role=self.role)
        if not isinstance(self.content, str):
            raise InvalidProviderMessageContent()
        if not isinstance(self.images, tuple) or any(
            not isinstance(image, ImageContent) for image in self.images
        ):
            raise InvalidProviderMessageContent()
        if self.images and self.role is not ProviderRole.USER:
            raise InvalidProviderMessageContent()


@dataclass(frozen=True, slots=True)
class ProviderResponse(ValueObject):
    output: str

    @classmethod
    def create(cls, output: str) -> ProviderResponse:
        obj = cls(output=output)
        obj._enforce_types()
        return obj

    def _enforce_types(self) -> None:
        if not isinstance(self.output, str):
            raise InvalidProviderResponseOutput()


@dataclass(frozen=True, slots=True)
class StructuredOutput(ValueObject):
    """A JSON schema the provider must constrain decoding to, not merely be told about.

    Prompt-embedded schemas are advisory: models drop required keys, invent
    extra ones and emit unbalanced JSON. Providers that support constrained
    decoding enforce this schema instead. ``schema_json`` is canonical JSON text
    so the value object stays immutable.
    """

    name: str
    schema_json: str

    @classmethod
    def create(cls, *, name: str, schema_json: str) -> StructuredOutput:
        obj = cls(name=name, schema_json=schema_json)
        obj._enforce_types()
        return obj

    def _enforce_types(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise InvalidInferenceParams(field="structured_output.name", reason="must be non-empty")
        if not isinstance(self.schema_json, str) or not self.schema_json.strip():
            raise InvalidInferenceParams(
                field="structured_output.schema_json", reason="must be non-empty"
            )


@dataclass(frozen=True, slots=True)
class InferenceParams(ValueObject):
    """Per-call limits. Sampling knobs (temperature, top_p) are deliberately absent:
    current Claude models reject them; reasoning depth is the provider's effort setting.
    """

    max_tokens: int = 1024
    structured_output: StructuredOutput | None = None

    @classmethod
    def create(
        cls,
        max_tokens: int = 1024,
        structured_output: StructuredOutput | None = None,
    ) -> InferenceParams:
        obj = cls(max_tokens=max_tokens, structured_output=structured_output)
        obj._enforce_types()
        return obj

    def _enforce_types(self) -> None:
        if isinstance(self.max_tokens, bool) or not isinstance(self.max_tokens, int):
            raise InvalidInferenceParams(field="max_tokens", reason="must be int")
        if self.max_tokens <= 0:
            raise InvalidInferenceParams(field="max_tokens", reason="must be > 0")
        if self.structured_output is not None and not isinstance(
            self.structured_output, StructuredOutput
        ):
            raise InvalidInferenceParams(
                field="structured_output", reason="must be StructuredOutput"
            )
