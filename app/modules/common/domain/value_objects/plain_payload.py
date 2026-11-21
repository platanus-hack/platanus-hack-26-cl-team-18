"""Strict readers for the JSON-plain payloads that value objects round-trip.

Value objects persist themselves with ``to_plain()`` and rebuild with
``from_plain()``. These helpers are the shared, fail-closed type checks those
``from_plain()`` implementations use, so a malformed stored payload raises with
the offending field name instead of producing a half-valid value object.
"""

from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID


def require_plain_object(payload: object, *, field: str) -> Mapping[str, object]:
    if not isinstance(payload, Mapping) or any(not isinstance(key, str) for key in payload):
        raise ValueError(f"{field} payload must be a JSON object")
    return payload


def require_plain_keys(data: Mapping[str, object], expected: set[str], *, field: str) -> None:
    if set(data) != expected:
        raise ValueError(f"{field} payload keys invalid")


def require_plain_str(payload: object, *, field: str) -> str:
    if not isinstance(payload, str):
        raise TypeError(f"{field} must be str")
    return payload


def require_plain_list(payload: object, *, field: str) -> list[object]:
    if not isinstance(payload, list):
        raise TypeError(f"{field} must be list")
    return payload


def require_plain_int(payload: object, *, field: str) -> int:
    if isinstance(payload, bool) or not isinstance(payload, int):
        raise TypeError(f"{field} must be int")
    return payload


def require_plain_uuid(payload: object, *, field: str) -> UUID:
    try:
        return UUID(require_plain_str(payload, field=field))
    except ValueError as exc:
        raise ValueError(f"{field} must be UUID") from exc


def optional_plain_str(payload: object, *, field: str) -> str | None:
    return None if payload is None else require_plain_str(payload, field=field)
