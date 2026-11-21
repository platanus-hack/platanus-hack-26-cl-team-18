from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import uuid4

import pytest

from app.modules.common.application.idempotency import (
    IdempotencyFingerprintNotDeclared,
    canonicalize_semantic_payload,
    compute_request_hash,
    idempotency_fingerprint_for,
)


class _Mode(StrEnum):
    ACTIVE = "active"


@dataclass(frozen=True, slots=True)
class _DefaultedCommand:
    include_default: bool

    def idempotency_semantic_payload(self) -> dict[str, object]:
        return {
            "name": "Acme",
            "reserve_price_cents": 0 if self.include_default else 0,  # noqa: RUF034 — both branches equal on purpose
        }


@dataclass(frozen=True, slots=True)
class _OrderAwareCommand:
    values: list[str]

    def idempotency_semantic_payload(self) -> dict[str, object]:
        return {"values": self.values}


def test_canonicalize_semantic_payload_ignores_mapping_key_order() -> None:
    left = {"b": 2, "a": {"z": 1, "y": 2}}
    right = {"a": {"y": 2, "z": 1}, "b": 2}

    assert canonicalize_semantic_payload(left) == canonicalize_semantic_payload(right)
    assert compute_request_hash(left) == compute_request_hash(right)


def test_canonicalize_semantic_payload_normalizes_unordered_sets() -> None:
    left = {"emails": {"b@example.com", "a@example.com"}}
    right = {"emails": {"a@example.com", "b@example.com"}}

    assert compute_request_hash(left) == compute_request_hash(right)


def test_canonicalize_semantic_payload_preserves_semantic_list_order() -> None:
    left = _OrderAwareCommand(["line-1", "line-2"])
    right = _OrderAwareCommand(["line-2", "line-1"])

    assert idempotency_fingerprint_for(left) != idempotency_fingerprint_for(right)


def test_canonicalize_semantic_payload_serializes_domain_primitives_stably() -> None:
    occurred_at = datetime(2026, 5, 12, 20, 0, 0, tzinfo=UTC)
    payload = {
        "organization_id": uuid4(),
        "effective_on": date(2026, 5, 12),
        "occurred_at": occurred_at,
        "amount": Decimal("1.00"),
        "mode": _Mode.ACTIVE,
    }

    canonical = canonicalize_semantic_payload(payload)

    assert canonical["effective_on"] == "2026-05-12"
    assert canonical["occurred_at"] == "2026-05-12T20:00:00.000Z"
    assert canonical["amount"] == "1.00"
    assert canonical["mode"] == "active"


def test_idempotency_fingerprint_for_requires_declared_semantic_payload() -> None:
    with pytest.raises(IdempotencyFingerprintNotDeclared, match="must define"):
        idempotency_fingerprint_for(object())  # type: ignore[arg-type]


def test_idempotency_fingerprint_for_rejects_non_mapping_payloads() -> None:
    @dataclass(frozen=True, slots=True)
    class _InvalidCommand:
        def idempotency_semantic_payload(self) -> dict[str, object]:  # type: ignore[override]
            return None  # type: ignore[return-value]

    with pytest.raises(TypeError, match="must return dict"):
        idempotency_fingerprint_for(_InvalidCommand())


def test_idempotency_fingerprint_for_treats_defaulted_payloads_as_equivalent() -> None:
    omitted_default = _DefaultedCommand(include_default=False)
    explicit_default = _DefaultedCommand(include_default=True)

    assert idempotency_fingerprint_for(omitted_default) == idempotency_fingerprint_for(
        explicit_default
    )


def test_idempotency_fingerprint_for_raises_when_semantic_payload_missing() -> None:
    @dataclass(frozen=True, slots=True)
    class _BadCommand:
        pass

    with pytest.raises(IdempotencyFingerprintNotDeclared):
        idempotency_fingerprint_for(_BadCommand())  # type: ignore[arg-type]
