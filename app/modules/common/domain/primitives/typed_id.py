"""`TypedId`: the base class for aggregate-identity value objects.

The backend passes a lot of UUIDs around — `screen_id`, `campaign_id`,
`moment_id`, `auction_id`, `creative_id` — and a bare `UUID` makes all of
them the same type. Nothing stops a screen id from being handed to a function
that wants a campaign id; the mistake surfaces later as an empty query result
or, worse, as a row attached to the wrong tenant.

A `TypedId` subclass is a distinct type carrying a single `UUID`. Two ids of
different classes never compare equal even when they wrap the same UUID, so
mixing them up is a type error at check time and a `False` at runtime rather
than a silent lookup miss.

Declaring one is a one-liner — no `@dataclass`, no `_enforce_types`, no
`create` override::

    class CampaignId(TypedId):
        \"\"\"Identifies a `Campaign` aggregate across its whole lifecycle.\"\"\"

        __slots__ = ()

`__slots__ = ()` is not required for correctness (frozen-ness comes from this
base) but keeps subclass instances dict-free, which matters when ids appear in
every row of a large ledger read.

Do **not** re-apply `@dataclass` to a subclass: that regenerates `__eq__` and
`__hash__` and drops the cross-type guard defined here.

Identity *creation* stays in the application layer. This module deliberately
offers no `new()`/`generate()` factory — the domain must receive ids, never
mint them (see `tests/unit/architecture/test_layer_isolation.py::
test_domain_layer_never_generates_ids`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Self
from uuid import UUID

from app.modules.common.domain.primitives.value_object import ValueObject


@dataclass(frozen=True, slots=True)
class TypedId(ValueObject):
    """A UUID that knows which kind of thing it identifies.

    Abstract by convention: instantiate a subclass, never `TypedId` itself.
    """

    value: UUID

    def __post_init__(self) -> None:
        # Direct construction is a supported path (`CampaignId(some_uuid)`), so
        # the invariant is enforced here rather than only inside `create`.
        if type(self) is TypedId:
            raise TypeError(
                "TypedId is abstract; instantiate a subclass such as CampaignId "
                "so the id carries what it identifies"
            )
        self._enforce_types()

    @classmethod
    def create(cls, value: UUID | str) -> Self:
        """Build an id from a `UUID`, or from its canonical string form.

        The string form exists for transport boundaries (path params, provider
        payloads, CSV rows). Anything else is a programming error, not a value
        error, and raises `TypeError`.
        """
        return cls(cls._normalize_uuid(value))

    @classmethod
    def from_optional(cls, value: UUID | str | None) -> Self | None:
        """`create` for a nullable slot: `None` in, `None` out.

        Nullable ids are everywhere at the boundaries — `winner_campaign_id` on
        an auction with no fill, `superseded_by_id` on a live creative, a `NULL`
        column coming back from a row. Without this, every one of those sites
        writes its own `X.create(v) if v is not None else None`, and the
        interesting part (which id type) gets buried in the ceremony.
        """
        if value is None:
            return None
        return cls.create(value)

    @classmethod
    def _normalize_uuid(cls, value: UUID | str) -> UUID:
        if isinstance(value, UUID):
            return value

        if isinstance(value, str):
            try:
                return UUID(value.strip())
            except ValueError as exc:
                raise ValueError(f"Invalid {cls.__name__}: {value!r}") from exc

        raise TypeError(
            f"{cls.__name__} must be built from UUID or str, got {type(value).__name__}"
        )

    def _enforce_types(self) -> None:
        if not isinstance(self.value, UUID):
            raise TypeError(
                f"{type(self).__name__}.value must be UUID; use {type(self).__name__}.create"
            )

    def __str__(self) -> str:
        return str(self.value)

    def __repr__(self) -> str:
        return f"{type(self).__name__}({str(self.value)!r})"

    def __eq__(self, other: object) -> bool:
        """Equal only to the same id type wrapping the same UUID.

        A bare `UUID` is intentionally *not* equal to a `TypedId`: allowing it
        would let untyped ids flow back in through comparisons and re-open the
        exact confusion this class exists to close.
        """
        if isinstance(other, TypedId):
            return type(self) is type(other) and self.value == other.value
        return NotImplemented

    def __hash__(self) -> int:
        # The type participates so that a set or dict holding several kinds of
        # id does not pile same-UUID entries into one bucket.
        return hash((type(self).__name__, self.value))


def uuid_of(id_: TypedId | None) -> UUID | None:
    """Unwrap a nullable id on the way *out* to a boundary that speaks `UUID`.

    Three boundaries cannot hold a `TypedId` and are not going to
    start: `Entity.id` and `DomainEvent.organization_id`/`aggregate_id` are
    validated as `UUID` and, in the event case, already serialized into outbox
    rows; the Postgres repositories bind SQL params; the HTTP DTOs are pydantic
    `UUID`. Typed ids therefore live *inside* the domain and unwrap at the edge.

    This is the counterpart to `TypedId.from_optional`, and exists for the same
    reason: so the edge reads as one conversion rather than a conditional whose
    `None` branch is easy to get subtly wrong.
    """
    return None if id_ is None else id_.value
