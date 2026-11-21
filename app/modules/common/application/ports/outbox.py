"""Outbox repository contract for reliable integration events.

Architectural intent:
- Persist integration events in the same transaction as domain changes.
- Keep delivery concerns in infrastructure/worker layers.
"""

from __future__ import annotations

from typing import Protocol

from app.modules.common.application.integration_event import IntegrationEvent


class IOutboxRepository(Protocol):
    """Persist integration events for reliable delivery (outbox pattern).

    Guarantees:
    - Called inside the same DB transaction as aggregate writes
    - Must be idempotent on (event_id)
    """

    def add(self, evt: IntegrationEvent) -> None: ...

    def add_all(self, evts: list[IntegrationEvent]) -> None: ...
