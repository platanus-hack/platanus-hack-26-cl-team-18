from __future__ import annotations

from app.modules.common.domain.primitives.errors import DomainError


class AIDomainValidationError(DomainError):
    """Base AI domain validation error."""


class InvalidToolName(AIDomainValidationError):
    """Tool names must be non-empty, trimmed strings."""

    def __init__(self, *, name: object) -> None:
        super().__init__(
            code="ai.invalid_tool_name",
            msg="Tool name must be a non-empty string.",
            params={"name": str(name)},
        )


class InvalidToolArguments(AIDomainValidationError):
    """Tool call arguments must be a dictionary."""

    def __init__(self) -> None:
        super().__init__(
            code="ai.invalid_tool_arguments",
            msg="Tool arguments must be a dictionary.",
            params={},
        )


class InvalidToolSchema(AIDomainValidationError):
    """Tool schema must be dictionary-based and valid for runtime validation."""

    def __init__(self, *, reason: str) -> None:
        super().__init__(
            code="ai.invalid_tool_schema",
            msg="Tool schema is invalid.",
            params={"reason": reason},
        )


class InvalidProviderMessageRole(AIDomainValidationError):
    """Provider role must be one of the supported values."""

    def __init__(self, *, role: object) -> None:
        super().__init__(
            code="ai.invalid_provider_message_role",
            msg="Provider message role is invalid.",
            params={"role": str(role)},
        )


class InvalidProviderMessageContent(AIDomainValidationError):
    """Provider message content must be string."""

    def __init__(self) -> None:
        super().__init__(
            code="ai.invalid_provider_message_content",
            msg="Provider message content must be a string.",
            params={},
        )


class InvalidProviderResponseOutput(AIDomainValidationError):
    """Provider response output must be string."""

    def __init__(self) -> None:
        super().__init__(
            code="ai.invalid_provider_response_output",
            msg="Provider response output must be a string.",
            params={},
        )


class InvalidInferenceParams(AIDomainValidationError):
    """Inference params must satisfy model constraints."""

    def __init__(self, *, field: str, reason: str) -> None:
        super().__init__(
            code="ai.invalid_inference_params",
            msg="Inference params are invalid.",
            params={"field": field, "reason": reason},
        )
