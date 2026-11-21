from __future__ import annotations


class AIApplicationError(Exception):
    """Base application-layer AI error."""


class MissingUserPromptError(AIApplicationError):
    """Raised when no user message is available for guardrails/execution."""


class PromptBlockedError(AIApplicationError):
    """Raised when guardrail validation blocks prompt execution."""


class ProviderCallError(AIApplicationError):
    """Raised when the LLM provider call fails after retries."""


class OutputParseError(AIApplicationError):
    """Raised when structured output cannot be parsed/validated."""


class AgentMaxStepsExceededError(AIApplicationError):
    """Raised when an agent exceeds configured tool loop steps."""


class InvalidToolDirectiveError(AIApplicationError):
    """Raised when model output tool directive is invalid."""
