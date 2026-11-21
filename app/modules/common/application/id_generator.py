from __future__ import annotations

from uuid import UUID

import uuid_utils  # Recommended library for high-performance UUIDv7 in Python


class IdGenerator:
    """Application-layer identity creation (UUIDv7)."""

    @staticmethod
    def new_uuid7() -> UUID:
        return UUID(str(uuid_utils.uuid7()))
