# `ai` — LLM kernel

A provider-agnostic way to call an LLM and get validated, typed output back.
It calls Claude (default `claude-haiku-5-5` at effort `low`) through the official
`anthropic` SDK, with constrained JSON-schema decoding (`output_config.format`)
and a cached system prompt. Keep system prompts frozen (no timestamps or
per-moment data) so they stay cacheable.

| Piece | File |
|---|---|
| `AIService`: `prompt()`, `generate()`, `structured_output()` with guardrail + retries | `application/service/llm.py` |
| Value objects: `ProviderMessage`, `InferenceParams`, `StructuredOutput`, `ProviderResponse` | `domain/llm/provider.py` |
| `build_message()`, `get_last_user_prompt()` | `domain/llm/service.py` |
| `ModelProfile` (model + effort; `describe()` is the provenance string to store) | `domain/model_capabilities.py` |
| Port `ILLMProvider` and parser protocol | `ports/provider.py`, `ports/parser.py` |
| Typed provider errors: `ProviderRateLimited`, `ProviderUnavailable`, `ProviderCreditExhausted`, `ProviderRequestRejected`, `ProviderOutputIncomplete` | `ports/errors.py` |
| `AnthropicProvider` (SDK retries transient failures; maps SDK exceptions to typed errors, never puts provider text in exceptions) | `infrastructure/adapters/llm/anthropic.py` |
| Env-based factory `build_llm_provider()` | `infrastructure/adapters/llm/factory.py` |
| Pydantic parser + strict schema builder | `infrastructure/parsers/pydantic_json.py` |
| Simple prompt guardrail | `llm/guardrail/` |

Configuration (only the factory reads it): `ANTHROPIC_API_KEY` (required),
`ANTHROPIC_MODEL`, `ANTHROPIC_EFFORT`, `ANTHROPIC_TIMEOUT`; the SDK reads
`ANTHROPIC_BASE_URL` itself. See `.env.example`.

## Typed output in five lines

```python
from pydantic import BaseModel

from app.modules.ai.application.service.llm import AIService
from app.modules.ai.domain.llm.provider import InferenceParams
from app.modules.ai.domain.llm.service import build_message
from app.modules.ai.infrastructure.adapters.llm.factory import build_llm_provider
from app.modules.ai.infrastructure.parsers.pydantic_json import (
    parse_pydantic_json,
    structured_output_for,
)


class MomentTags(BaseModel):
    segment_ids: list[str]
    confidence: float
    reasoning: str


async def tag_moment(description: str) -> MomentTags:
    async with AIService(build_llm_provider()) as ai:
        return await ai.structured_output(
            build_message(
                system_prompt="Tag the moment with IAB segments.", user_prompt=description
            ),
            params=InferenceParams.create(structured_output=structured_output_for(MomentTags)),
            schema=MomentTags,
            parser=parse_pydantic_json,
        )
```

Rules that still apply here:

- Application code takes an `AIService` / `ILLMProvider`; only `app/bootstrap/`
  calls `build_llm_provider()`. Tests pass a fake provider (see
  `tests/unit/ai/application/test_ai_service.py`).
- Store `ModelProfile.describe()`, the confidence and the reasoning with every
  AI decision. Route low confidence to review or a safe fallback; AI output never
  defines truth by itself.
- Never log prompts that contain personal data, and never log the API key.
