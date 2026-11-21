# AI-GUARD: DO NOT MODIFY
from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class Entity:
    """Base class for domain Entities (non-aggregate roots).

    An Entity represents a domain concept that:
    - has a stable identity (`id`)
    - exists over time
    - lives *inside* an Aggregate Root boundary

    Entities do NOT define transactional boundaries and MUST NOT
    emit DomainEvents directly.

    ----------------------------------------------------------------------
    When to use an Entity
    ----------------------------------------------------------------------

    Use an Entity when:
    - the concept has identity (two instances with the same `id`
      represent the same real-world thing)
    - the concept is part of a larger Aggregate Root
    - the concept’s lifecycle is managed by its owning aggregate

    Examples:
    - CreativeSlot inside Creative
    - BudgetPacing inside Campaign
    - RuleCondition inside RuleGroup

    If the object has no identity and is defined purely by its values,
    it should be a Value Object instead.

    ----------------------------------------------------------------------
    Entity vs Aggregate Root
    ----------------------------------------------------------------------

    - AggregateRoot:
        * owns the consistency boundary
        * emits DomainEvents
        * is loaded/saved via repositories

    - Entity:
        * belongs to exactly one Aggregate Root
        * never emits DomainEvents
        * is created, updated, and deleted by its aggregate

    ----------------------------------------------------------------------
    Identity & equality
    ----------------------------------------------------------------------

    Entity equality is identity-based:
    - two entities are equal if and only if:
        * they are the same concrete type
        * they have the same `id`

    Structural equality (comparing all fields) is intentionally avoided.

    ----------------------------------------------------------------------
    Mutability model
    ----------------------------------------------------------------------

    Entities are defined as immutable (`frozen=True`) by default.

    Updates should be modeled as:
    - creating a new Entity instance with the same `id`
    - replacing it inside the owning aggregate

    This aligns with:
    - immutable aggregate patterns
    - deterministic event emission
    - easier reasoning and testing

    ----------------------------------------------------------------------
    What Entities MUST NOT do
    ----------------------------------------------------------------------

    - Emit DomainEvents
    - Access repositories or external services
    - Call clocks or generate timestamps
    - Contain cross-aggregate logic
    - Be modified outside their owning Aggregate Root

    ----------------------------------------------------------------------
    Summary
    ----------------------------------------------------------------------

    Entities model identity *within* an aggregate.
    Aggregate Roots coordinate behavior and events.
    Value Objects model pure values.

    Keep Entities small, focused, and subordinate to their Aggregate Root.

    """

    id: UUID

    def __post_init__(self) -> None:
        if not isinstance(self.id, UUID):
            raise TypeError("Entity.id must be UUID")

    @property
    def entity_type(self) -> str:
        return type(self).__name__

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Entity) and self.id == other.id and type(self) is type(other)

    def __hash__(self) -> int:
        # hashable for sets/dicts, identity semantics
        return hash((type(self), self.id))
