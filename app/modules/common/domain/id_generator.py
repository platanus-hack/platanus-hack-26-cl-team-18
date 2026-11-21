"""Domain-layer identity creation (UUIDv7).

Can be imported by domain code without violating layer isolation.
"""

from __future__ import annotations

from uuid import UUID

import uuid_utils  # Recommended library for high-performance UUIDv7 in Python


class DomainIdGenerator:
    """Domain-layer identity creation (UUIDv7)."""

    @staticmethod
    def new_uuid7() -> UUID:
        return UUID(str(uuid_utils.uuid7()))
