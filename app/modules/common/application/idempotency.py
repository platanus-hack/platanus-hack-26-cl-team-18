from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, fields, is_dataclass
from datetime import date, datetime, time
from decimal import Decimal
from enum import Enum, StrEnum
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from app.modules.common.application.parsers.timestamps import format_utc_datetime
from app.modules.common.domain.primitives.errors import DomainError


class IdempotencyStatus(StrEnum):
    BEGIN_OK = "begin_ok"  # acquired lock, caller should proceed
    IN_PROGRESS = "in_progress"  # someone else is processing
    COMPLETED = "completed"  # return cached result
    CONFLICT = "conflict"  # same key, different request


@dataclass(frozen=True, slots=True)
class IdempotencyBeginResult:
    status: IdempotencyStatus
    cached_result: dict[str, Any] | None = None
    #: Fencing token of the reservation this caller now holds. Set only on
    #: ``BEGIN_OK``. A caller that completes or releases the reservation in a
    #: *later* transaction, through a new repository instance, must hand it
    #: back as ``lease_token=``. Otherwise a retry that reclaimed the stale
    #: reservation in between cannot be told apart from the original owner.
    lease_token: UUID | None = None


class IdempotencyConflictReason(StrEnum):
    PAYLOAD_MISMATCH = "payload_mismatch"
    IN_PROGRESS = "in_progress"
    MISSING_RESULT = "missing_result"
    LEASE_LOST = "lease_lost"
    UNSPECIFIED = "unspecified"


class IdempotencyConflictError(DomainError):
    """Raised when the same idempotency key is used with a different payload."""

    CODE = "idempotency.conflict"

    def __init__(
        self,
        msg: str = "Idempotency-Key conflict: payload mismatch",
        *,
        reason: IdempotencyConflictReason = IdempotencyConflictReason.UNSPECIFIED,
    ) -> None:
        self.reason = reason
        super().__init__(code=self.CODE, msg=msg)


class IdempotencyLeaseLostError(IdempotencyConflictError):
    """The reservation was reclaimed by a retry before its owner completed it.

    Raised by ``store_result`` when the caller's lease token no longer names
    the ``in_progress`` holder. A later attempt took over the stale
    reservation, and its result is the one the key replays. The displaced
    owner must not overwrite it. Its side effect also ran a second time,
    which is what an operator needs to see.
    """

    CODE = "idempotency.lease_lost"

    def __init__(
        self,
        msg: str = "Idempotency-Key conflict: reservation was reclaimed by a retry",
    ) -> None:
        self.reason = IdempotencyConflictReason.LEASE_LOST
        DomainError.__init__(self, code=self.CODE, msg=msg)


class IIdempotencyRepository(Protocol):
    """Reservation register behind every idempotent write.

    ``lease_token`` fences completion and release. An implementation remembers
    the token of each reservation it created or reclaimed. Callers that finish
    through a *different* instance (split-transaction flows) pass the token
    from ``IdempotencyBeginResult.lease_token``.
    """

    def try_begin(self, key: str, request_hash: str) -> IdempotencyBeginResult: ...
    def store_result(
        self,
        key: str,
        request_hash: str,
        result: dict[str, Any] | None,
        *,
        lease_token: UUID | None = None,
    ) -> None: ...
    def get_result(self, key: str) -> dict[str, Any] | None: ...
    def reset_in_progress(self, key: str, *, lease_token: UUID | None = None) -> None: ...


@runtime_checkable
class ReleasableIdempotencyReservation(Protocol):
    """Optional repository capability: drop a reservation without a rollback.

    ``reset_in_progress`` is the *failure* path: the command raised, the
    transaction is usually aborted, so implementations roll back before
    deleting the reservation. A command that *returned* a deliberately
    non-final result (``should_cache_result`` is False) is different -- its
    writes are legitimate (e.g. a scheduled job's deferral) and must commit
    with the caller's unit of work. ``release_reservation`` deletes only this
    key's in_progress row inside the current transaction and never rolls back.

    Repositories that do not implement it keep the ``reset_in_progress``
    fallback, which is correct for them because they share no transaction
    with the command's writes.
    """

    def release_reservation(self, key: str, *, lease_token: UUID | None = None) -> None: ...


@runtime_checkable
class DiscardableIdempotencyReceipt(Protocol):
    """Optional repository capability: delete one completed receipt.

    Used only to self-heal receipts that should never have been cached (a
    non-final result stored before its command declared
    ``should_cache_result``). The delete is scoped to ``key`` *and*
    ``request_hash`` so it cannot remove a different request's receipt.
    Returns whether a row was removed.
    """

    def discard_completed_result(self, key: str, request_hash: str) -> bool: ...


class SupportsIdempotencyFingerprint(Protocol):
    """Write-command contract for semantic idempotency payload declaration."""

    def idempotency_semantic_payload(self) -> dict[str, object]: ...


class IdempotencyFingerprintNotDeclared(TypeError):
    """Raised when a migrated write command does not declare its semantic payload."""


def canonicalize_semantic_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Canonicalize a semantic write payload before hashing for idempotency."""
    normalized = _normalize_for_json(payload)
    if not isinstance(normalized, dict):
        raise TypeError("idempotency semantic payload must normalize to a JSON object")
    return normalized


def idempotency_fingerprint_for(command: SupportsIdempotencyFingerprint) -> str:
    """Build the canonical idempotency fingerprint for a migrated write command."""
    if hasattr(command, "idempotency_semantic_payload"):
        payload = command.idempotency_semantic_payload()
    else:
        raise IdempotencyFingerprintNotDeclared(
            f"{type(command).__name__} must define idempotency_semantic_payload()",
        )
    if not isinstance(payload, dict):
        raise TypeError(
            f"{type(command).__name__}.idempotency semantic payload must return dict[str, object]",
        )
    return compute_request_hash(canonicalize_semantic_payload(payload))


def compute_request_hash(payload: dict[str, Any]) -> str:
    """Deterministic hash for idempotency payloads."""
    normalized = canonicalize_semantic_payload(payload)
    encoded = json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _normalize_for_json(value: Any) -> Any:
    """Convert complex objects into JSON-friendly primitives.

    Not interchangeable with ``common.domain.canonical_hash.normalize_for_json``:
    datetimes here are UTC millisecond ``...Z`` strings (naive rejected) rather
    than ``isoformat()``, and ``to_plain()`` / pydantic / dataclass values are
    expanded rather than ``str()``-ed. Request hashes built from this output are
    persisted in the idempotency store, so the rules are frozen; see that
    function's docstring for the full comparison.
    """
    simple_types = (str, int, float, bool, type(None))

    if isinstance(value, simple_types):
        return value
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return format_utc_datetime(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, time):
        return value.isoformat()
    if hasattr(value, "to_plain") and callable(value.to_plain):
        return _normalize_for_json(value.to_plain())
    if hasattr(value, "model_dump") and callable(value.model_dump):
        return _normalize_for_json(value.model_dump(mode="json"))
    if is_dataclass(value):
        return {
            field.name: _normalize_for_json(getattr(value, field.name)) for field in fields(value)
        }
    if isinstance(value, dict):
        return {
            str(k): _normalize_for_json(v)
            for k, v in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_normalize_for_json(item) for item in value]
    if isinstance(value, (set, frozenset)):
        normalized_items = [_normalize_for_json(item) for item in value]
        return sorted(
            normalized_items,
            key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":")),
        )
    return str(value)
