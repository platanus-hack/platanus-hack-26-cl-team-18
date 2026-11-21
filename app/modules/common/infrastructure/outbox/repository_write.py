from __future__ import annotations

import json
import logging

from psycopg2.extensions import connection as PGConnection
from psycopg2.extras import Json

from app.modules.common.application.integration_event import IntegrationEvent
from app.modules.common.application.ports.outbox import IOutboxRepository
from app.modules.common.infrastructure.postgres.base import execute_many

logger = logging.getLogger(__name__)

# Maximum allowed JSON-serialized size of an outbox event payload (256 KB).
# Oversized payloads indicate a bug (e.g. embedding raw bytes or large lists)
# and would bloat the outbox table and downstream consumers.
_MAX_PAYLOAD_BYTES = 256 * 1024


def _assert_payload_size(evt: IntegrationEvent) -> None:
    size = len(json.dumps(evt.payload).encode())
    if size > _MAX_PAYLOAD_BYTES:
        logger.error(
            "outbox.payload_too_large",
            extra={
                "event_id": str(evt.event_id),
                "event_type": evt.event_type,
                "payload_bytes": size,
                "max_bytes": _MAX_PAYLOAD_BYTES,
                "aggregate_id": str(evt.aggregate_id) if evt.aggregate_id else None,
                "aggregate_type": evt.aggregate_type,
            },
        )
        raise ValueError(
            f"IntegrationEvent payload too large: {size} bytes "
            f"(max {_MAX_PAYLOAD_BYTES}). Event type: {evt.event_type}",
        )


class OutboxRepositoryPostgres(IOutboxRepository):
    """Postgres outbox repository (writes only)."""

    def __init__(self, conn: PGConnection) -> None:
        self.conn = conn

    def add(self, evt: IntegrationEvent) -> None:
        if evt.aggregate_id is None or evt.aggregate_type is None:
            raise ValueError("IntegrationEvent must include aggregate_id and aggregate_type")
        _assert_payload_size(evt)

        params = self._build_params(evt)
        with self.conn.cursor() as cur:
            cur.execute(self._insert_query(), params)

        logger.debug(
            "outbox.event_persisted",
            extra={
                "event_id": str(evt.event_id),
                "event_type": evt.event_type,
                "aggregate_id": str(evt.aggregate_id),
                "aggregate_type": evt.aggregate_type,
            },
        )

    def add_all(self, evts: list[IntegrationEvent]) -> None:
        if not evts:
            return

        for evt in evts:
            _assert_payload_size(evt)

        params: list[dict] = [self._build_params(evt) for evt in evts]
        with self.conn.cursor() as cur:
            execute_many(cur, self._insert_query(), params)

        logger.debug(
            "outbox.batch_persisted",
            extra={
                "batch_size": len(evts),
                "event_types": list({e.event_type for e in evts}),
            },
        )

    @staticmethod
    def _insert_query() -> str:
        query = """
            INSERT INTO outbox_event (
                event_id,
                event_type,
                schema_version,
                occurred_at,
                organization_id,
                aggregate_id,
                aggregate_type,
                aggregate_version,
                payload,
                headers,
                correlation_id,
                causation_id,
                request_id,
                idempotency_key,
                source,
                actor_type,
                actor_id
            )
            VALUES (
                %(event_id)s,
                %(event_type)s,
                %(schema_version)s,
                %(occurred_at)s,
                %(organization_id)s,
                %(aggregate_id)s,
                %(aggregate_type)s,
                %(aggregate_version)s,
                %(payload)s,
                %(headers)s,
                %(correlation_id)s,
                %(causation_id)s,
                %(request_id)s,
                %(idempotency_key)s,
                %(source)s,
                %(actor_type)s,
                %(actor_id)s
            )
            ON CONFLICT (event_id) DO NOTHING
        """
        return query

    @staticmethod
    def _build_params(evt: IntegrationEvent) -> dict:
        return {
            "event_id": str(evt.event_id),
            "event_type": evt.event_type,
            "schema_version": evt.schema_version,
            "occurred_at": evt.occurred_at,
            "organization_id": str(evt.organization_id) if evt.organization_id else None,
            "aggregate_id": str(evt.aggregate_id),
            "aggregate_type": evt.aggregate_type,
            "aggregate_version": evt.aggregate_version,
            "payload": Json(evt.payload),
            "headers": Json(evt.headers),
            "correlation_id": str(evt.correlation_id) if evt.correlation_id else None,
            "causation_id": str(evt.causation_id) if evt.causation_id else None,
            "request_id": str(evt.request_id) if evt.request_id else None,
            "idempotency_key": evt.idempotency_key,
            "source": evt.source,
            "actor_type": evt.actor_type,
            "actor_id": evt.actor_id,
        }
