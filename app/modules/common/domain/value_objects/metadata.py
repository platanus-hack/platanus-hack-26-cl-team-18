"""Shared JSON metadata value object with deterministic canonical representation."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from types import MappingProxyType
from typing import ClassVar, Self, TypeAlias

from app.modules.common.domain.primitives.value_object import ValueObject

JSONScalar: TypeAlias = str | int | float | bool | None
JSONValue: TypeAlias = JSONScalar | tuple["JSONValue", ...] | Mapping[str, "JSONValue"]


class MetadataError(ValueError):
    """Raised when metadata violates JSON/size/depth invariants."""


def _is_finite_float(value: float) -> bool:
    return value == value and value not in (float("inf"), float("-inf"))


def _estimate_size(value: JSONScalar) -> int:
    """Rough size of one scalar, used as an invariant guardrail.

    This is not exact byte size; it is a deterministic approximation that avoids
    repeated serialization while still protecting from oversized payloads.

    Only scalars and mapping keys are charged, each exactly once, as the tree is
    frozen. Containers are never re-measured on close: that charged a leaf once
    per enclosing level, so the effective ceiling shrank with nesting depth and
    an ~11 KB routing snapshot six levels deep was rejected as over 64 KB.
    """
    if value is None:
        return 4
    if isinstance(value, bool):
        return 5
    if isinstance(value, int):
        return len(str(value))
    if isinstance(value, float):
        return len(repr(value))
    return len(value)


def _freeze_json(
    value: object,
    *,
    path: str,
    depth: int,
    max_depth: int,
    total_size: list[int],
    max_total_size: int | None,
    max_str_len: int,
) -> JSONValue:
    if depth > max_depth:
        raise MetadataError(f"metadata too deep at {path!r} (max_depth={max_depth})")

    if value is None or isinstance(value, (str, bool, int)):
        if isinstance(value, str) and len(value) > max_str_len:
            raise MetadataError(
                f"metadata string too long at {path!r} (len={len(value)} max={max_str_len})",
            )
        total_size[0] += _estimate_size(value)
        if max_total_size is not None and total_size[0] > max_total_size:
            raise MetadataError(f"metadata too large (max_total_size={max_total_size})")
        return value

    if isinstance(value, float):
        if not _is_finite_float(value):
            raise MetadataError(f"metadata contains non-finite float at {path!r}")
        total_size[0] += _estimate_size(value)
        if max_total_size is not None and total_size[0] > max_total_size:
            raise MetadataError(f"metadata too large (max_total_size={max_total_size})")
        return value

    if isinstance(value, Mapping):
        frozen: dict[str, JSONValue] = {}
        for key, sub_value in value.items():
            if not isinstance(key, str):
                raise MetadataError(
                    f"metadata key must be str at {path!r}, got {type(key).__name__}",
                )
            frozen[key] = _freeze_json(
                sub_value,
                path=f"{path}.{key}",
                depth=depth + 1,
                max_depth=max_depth,
                total_size=total_size,
                max_total_size=max_total_size,
                max_str_len=max_str_len,
            )
        ordered = dict(sorted(frozen.items(), key=lambda item: item[0]))
        total_size[0] += sum(len(key) for key in ordered)
        if max_total_size is not None and total_size[0] > max_total_size:
            raise MetadataError(f"metadata too large (max_total_size={max_total_size})")
        return MappingProxyType(ordered)

    if isinstance(value, (list, tuple)):
        frozen_seq = tuple(
            _freeze_json(
                sub_value,
                path=f"{path}[{index}]",
                depth=depth + 1,
                max_depth=max_depth,
                total_size=total_size,
                max_total_size=max_total_size,
                max_str_len=max_str_len,
            )
            for index, sub_value in enumerate(value)
        )
        return frozen_seq

    raise MetadataError(f"metadata value not JSON-serializable at {path!r}: {type(value).__name__}")


def _thaw_json(value: JSONValue) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw_json(sub_value) for key, sub_value in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class Metadata(ValueObject):
    """JSON-only metadata value object with deterministic canonical serialization.

    Architectural intent:
    - Centralize metadata invariants across bounded contexts.
    - Prevents primitive obsession by replacing raw dict[str, object].
    """

    value: MappingProxyType[str, JSONValue]

    _VO_NAME: ClassVar[str] = "Metadata"
    MAX_KEYS: ClassVar[int] = 50
    MAX_DEPTH: ClassVar[int] = 6
    MAX_TOTAL_SIZE: ClassVar[int] = 64_000
    MAX_STR_LEN: ClassVar[int] = 4_096

    @classmethod
    def create(cls, value: Mapping[str, object] | Metadata | None = None) -> Self:
        """Create immutable metadata and enforce JSON/depth/size guardrails."""
        if isinstance(value, cls):
            return value
        if value is None:
            value = {}
        if not isinstance(value, Mapping):
            raise TypeError("Metadata value must be Mapping[str, JSON-compatible value]")
        if len(value) > cls.MAX_KEYS:
            raise MetadataError(f"metadata has too many keys (len={len(value)} max={cls.MAX_KEYS})")

        total_size = [0]
        frozen = _freeze_json(
            value,
            path="$",
            depth=0,
            max_depth=cls.MAX_DEPTH,
            total_size=total_size,
            max_total_size=cls.MAX_TOTAL_SIZE,
            max_str_len=cls.MAX_STR_LEN,
        )
        if not isinstance(frozen, MappingProxyType):
            raise MetadataError("metadata root must be a mapping/object")

        obj = cls(value=frozen)
        obj._enforce_types()
        return obj

    @classmethod
    def from_persisted(cls, value: Mapping[str, object]) -> Self:
        """Rehydrate stored metadata without re-applying the total-size write cap.

        The size cap guards what a process may *write*.  A row already stored is
        a fact; re-checking it on read means a later release that writes larger
        rows breaks every process still running the older limit, during the
        deploy and after a rollback (expand/contract, CLAUDE.md).  Every other
        guardrail -- JSON-only values, depth, key count, string length -- still
        applies.
        """
        if not isinstance(value, Mapping):
            raise TypeError("Metadata value must be Mapping[str, JSON-compatible value]")
        if len(value) > cls.MAX_KEYS:
            raise MetadataError(f"metadata has too many keys (len={len(value)} max={cls.MAX_KEYS})")
        frozen = _freeze_json(
            value,
            path="$",
            depth=0,
            max_depth=cls.MAX_DEPTH,
            total_size=[0],
            max_total_size=None,
            max_str_len=cls.MAX_STR_LEN,
        )
        if not isinstance(frozen, MappingProxyType):
            raise MetadataError("metadata root must be a mapping/object")
        return cls(value=frozen)

    def _enforce_types(self) -> None:
        if not isinstance(self.value, MappingProxyType):
            raise TypeError("Metadata.value must be MappingProxyType; use Metadata.create")
        if len(self.value) > self.MAX_KEYS:
            raise MetadataError(
                f"metadata has too many keys (len={len(self.value)} max={self.MAX_KEYS})",
            )

        total_size = [0]
        _freeze_json(
            self.value,
            path="$",
            depth=0,
            max_depth=self.MAX_DEPTH,
            total_size=total_size,
            max_total_size=self.MAX_TOTAL_SIZE,
            max_str_len=self.MAX_STR_LEN,
        )

    @staticmethod
    def empty() -> Metadata:
        return Metadata.create({})

    def get(self, key: str, default: JSONValue | None = None) -> JSONValue | None:
        """Read one metadata key without mutating the value object."""
        return self.value.get(key, default)

    def with_(self, key: str, value: object) -> Metadata:
        """Return a new metadata VO with one key added or replaced."""
        merged: dict[str, object] = dict(self.value)
        merged[key] = value
        return Metadata.create(merged)

    def merge(self, other: Metadata) -> Metadata:
        """Return a new metadata VO with other keys overriding collisions."""
        merged: dict[str, object] = dict(self.value)
        merged.update(other.value)
        return Metadata.create(merged)

    def to_plain(self) -> dict[str, object]:
        """Convert immutable internal storage into plain JSON-compatible containers."""
        return {key: _thaw_json(sub_value) for key, sub_value in self.value.items()}

    def canonical_json(self) -> str:
        """Serialize metadata as deterministic JSON (sorted keys, compact separators)."""
        # Hash-consolidation audit: FROZEN, not byte-identical to common.domain.canonical_hash:
        # ensure_ascii=False and allow_nan=False. Changing the bytes re-keys stored values.
        return json.dumps(
            self.to_plain(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )

    def stable_hash(self) -> str:
        """Return SHA-256 of canonical_json() for stable idempotency/debug fingerprints."""
        return sha256(self.canonical_json().encode("utf-8")).hexdigest()
