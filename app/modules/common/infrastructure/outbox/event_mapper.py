from __future__ import annotations

from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any, ClassVar
from uuid import UUID

from app.modules.common.application.integration_event import IntegrationEvent
from app.modules.common.application.parsers.timestamps import format_utc_datetime
from app.modules.common.domain.primitives.events import DomainEvent
from app.modules.common.domain.primitives.value_object import ValueObject


class DomainEventToIntegrationEventMapper:
    """DomainEvent -> IntegrationEvent

    Rules:
    - IntegrationEvent fields carry envelope metadata (ids, routing, tracing, actor).
    - IntegrationEvent.payload contains ONLY event-specific domain data (no ctx/actor/ids duplication).
    - Payload must be JSON-safe and deterministic.
    """

    _BASE_EVENT_FIELDS: ClassVar[set[str]] = {
        "event_id",
        "organization_id",
        "occurred_at",
        "aggregate_id",
        "aggregate_version",
        "actor",
        "ctx",
    }

    def map(self, evt: DomainEvent) -> IntegrationEvent:
        return IntegrationEvent(
            # routing/identity
            event_id=evt.event_id,
            event_type=evt.event_type,
            schema_version=evt.schema_version,
            occurred_at=evt.occurred_at,
            # tenant + aggregate
            organization_id=evt.organization_id,
            aggregate_type=evt.aggregate_type,
            aggregate_id=evt.aggregate_id,
            aggregate_version=evt.aggregate_version,
            # event-specific data
            payload=self._extract_payload(evt),
            # tracing
            correlation_id=evt.ctx.correlation_id,
            causation_id=evt.ctx.causation_id,  # keep None if not provided (or set fallback if you want)
            request_id=evt.ctx.request_id,
            idempotency_key=evt.ctx.idempotency_key,
            source=evt.ctx.source,
            # actor
            actor_type=evt.actor.type.value,
            actor_id=str(evt.actor.id) if evt.actor.id is not None else None,
            # recommended extension point (needs to exist on IntegrationEvent)
            headers=self._json_safe(evt.ctx.metadata.to_plain()),
        )

    def _extract_payload(self, evt: DomainEvent) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        for f in fields(evt):
            name = f.name
            if name in self._BASE_EVENT_FIELDS or name.startswith("_"):
                continue
            payload[name] = self._json_safe(getattr(evt, name))
        return payload

    def _json_safe(self, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, Enum):
            return self._json_safe(value.value)
        if isinstance(value, ValueObject):
            if hasattr(value, "to_plain") and callable(value.to_plain):
                return self._json_safe(value.to_plain())
            if hasattr(value, "value"):
                return self._json_safe(value.value)
            if is_dataclass(value):
                return self._json_safe(self._dataclass_to_dict(value))
            return str(value)
        if is_dataclass(value):
            return self._json_safe(self._dataclass_to_dict(value))
        if isinstance(value, UUID):
            return str(value)
        if isinstance(value, datetime):
            return format_utc_datetime(value)
        if isinstance(value, date):
            return value.isoformat()
        if isinstance(value, Decimal):
            return str(value)
        if isinstance(value, (list, tuple)):
            return [self._json_safe(v) for v in value]
        if isinstance(value, Mapping):
            return {str(k): self._json_safe(v) for k, v in value.items()}
        return value

    def _dataclass_to_dict(self, value: Any) -> dict[str, Any]:
        return {
            field.name: self._json_safe(getattr(value, field.name))
            for field in fields(value)
            if not field.name.startswith("_")
        }
