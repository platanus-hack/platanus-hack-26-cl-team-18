"""Tenancy identifier, shared by every bounded context.

`OrganizationId` lives in `common/` rather than in `organization/` on purpose:
every module scopes its reads and writes by `organization_id` (§9), so
no single bounded context owns it in practice. `common/` is a permanent leaf —
nothing here may import another bounded context — which keeps the id reachable
from all of them without inverting a dependency.

There is deliberately no `EntityId`: the organization *is* the tenant.

Ids that *are* owned by one context stay in that context's
`domain/value_objects/ids.py`; promote to `common/` only when a real import
cycle demonstrates the need.
"""

from __future__ import annotations

from app.modules.common.domain.primitives.typed_id import TypedId


class OrganizationId(TypedId):
    """Identifies the tenant. The scoping key for every query in the system."""

    __slots__ = ()
