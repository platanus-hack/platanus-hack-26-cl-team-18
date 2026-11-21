from __future__ import annotations

import logging
from types import TracebackType
from typing import Any

from pydantic import BaseModel

from app.modules.ai.application.errors import (
    MissingUserPromptError,
    OutputParseError,
    PromptBlockedError,
    ProviderCallError,
)
from app.modules.ai.domain.llm.provider import InferenceParams, ProviderMessage, ProviderResponse
from app.modules.ai.domain.llm.service import build_message, get_last_user_prompt
from app.modules.ai.llm.guardrail.service import GuardrailService
from app.modules.ai.ports.parser import LLMOutputParser
from app.modules.ai.ports.provider import ILLMProvider


class AIService:
    """Reusable application service for provider-backed AI generation."""

    def __init__(
        self,
        provider: ILLMProvider,
        guardrail: GuardrailService | None = None,
        logger: logging.Logger | None = None,
        fail_open_on_guardrail_error: bool = False,
    ) -> None:
        self.provider = provider
        self.guardrail = guardrail
        self.logger = logger or logging.getLogger(__name__)
        self.fail_open_on_guardrail_error = fail_open_on_guardrail_error

    async def structured_output(
        self,
        messages: list[ProviderMessage],
        params: InferenceParams | None = None,
        schema: type[BaseModel] | None = None,
        force_return: bool = False,
        max_retries: int = 1,
        parser: LLMOutputParser | None = None,
    ) -> Any:
        if max_retries <= 0:
            max_retries = 1

        last_prompt, ok = get_last_user_prompt(messages)
        if not ok or last_prompt is None:
            raise MissingUserPromptError("No valid user prompt found")

        if self.guardrail:
            try:
                gr_resp = await self.guardrail.validate_prompt(last_prompt)
            except Exception as exc:
                if self.fail_open_on_guardrail_error:
                    self.logger.warning(
                        "guardrail_validation_failed_open",
                        extra={"reason": str(exc)},
                    )
                else:
                    raise PromptBlockedError(f"Guardrail validation error: {exc}") from exc
            else:
                if not gr_resp.is_valid:
                    raise PromptBlockedError(gr_resp.reason or "Prompt validation failed")

        response: ProviderResponse | None = None
        last_err: Exception | None = None

        for attempt in range(max_retries):
            try:
                response = await self.provider.complete(messages, params)
                break
            except Exception as exc:
                last_err = exc
                self.logger.error(
                    "provider_call_failed",
                    extra={
                        "attempt": attempt + 1,
                        "max_retries": max_retries,
                        "reason": str(exc),
                    },
                )

        if response is None:
            raise ProviderCallError(f"Failed after {max_retries} attempts: {last_err}")

        if parser is None:
            self.logger.warning("llm_parser_missing_returning_raw_output")
            return response.output

        try:
            return parser(response.output, schema, force_return)
        except Exception as exc:
            raise OutputParseError(f"Failed to parse structured output: {exc}") from exc

    async def prompt(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        params: InferenceParams | None = None,
    ) -> str:
        messages = build_message(system_prompt=system_prompt, user_prompt=user_prompt)
        return await self.generate(messages=messages, params=params)

    async def generate(
        self,
        messages: list[ProviderMessage],
        params: InferenceParams | None = None,
    ) -> str:
        try:
            response = await self.provider.complete(messages, params)
        except Exception as exc:
            self.logger.error(
                "provider_call_failed",
                extra={"reason": str(exc)},
            )
            raise ProviderCallError(f"Failed to call provider: {exc}") from exc

        if not response.output:
            self.logger.warning("provider_returned_empty_output")
            return ""

        self.logger.info(
            "provider_call_succeeded",
            extra={"output_length": len(response.output)},
        )
        return response.output

    async def aclose(self) -> None:
        await self.provider.aclose()

    async def __aenter__(self) -> AIService:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()
