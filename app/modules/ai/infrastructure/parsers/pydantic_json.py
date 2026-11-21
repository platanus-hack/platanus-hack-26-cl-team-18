"""Turn a pydantic model into a strict schema, and model output back into the model.

``AIService.structured_output`` takes a ``parser``; ``parse_pydantic_json`` is the
default one. ``structured_output_for`` builds the ``StructuredOutput`` that makes
the provider constrain decoding to the same model, so the schema the model sees
and the schema we validate against can never drift.
"""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel

from app.modules.ai.domain.llm.provider import StructuredOutput

_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


def parse_pydantic_json(
    s: str,
    schema: type[BaseModel] | None = None,
    force_return: bool = False,
) -> Any:
    """Parse JSON text (optionally inside a ```json fence) and validate it.

    With no ``schema`` the decoded JSON is returned. With ``force_return`` a
    validation failure returns the raw decoded JSON instead of raising.
    """
    match = _FENCE.match(s)
    data = json.loads(match.group(1) if match else s)
    if schema is None:
        return data
    try:
        return schema.model_validate(data)
    except Exception:
        if force_return:
            return data
        raise


def strict_json_schema(model: type[BaseModel]) -> dict[str, Any]:
    """The model's JSON schema in the strict shape structured outputs accept.

    Constrained decoding requires every object to list all its properties as required
    and to forbid extra keys. Optional fields stay nullable through their type.
    """
    schema = model.model_json_schema()
    _tighten(schema)
    for definition in schema.get("$defs", {}).values():
        _tighten(definition)
    return schema


def structured_output_for(model: type[BaseModel]) -> StructuredOutput:
    return StructuredOutput.create(
        name=model.__name__,
        schema_json=json.dumps(strict_json_schema(model), sort_keys=True),
    )


def _tighten(node: Any) -> None:
    if isinstance(node, dict):
        if node.get("type") == "object" and "properties" in node:
            node["required"] = list(node["properties"])
            node["additionalProperties"] = False
        node.pop("default", None)
        for key, value in node.items():
            if key != "$defs":
                _tighten(value)
    elif isinstance(node, list):
        for item in node:
            _tighten(item)


__all__ = ["parse_pydantic_json", "strict_json_schema", "structured_output_for"]
