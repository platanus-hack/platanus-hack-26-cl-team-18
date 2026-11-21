"""Where charges are recorded. Append-only; implementations never block the hot path."""

from typing import Protocol

from app.modules.delivery.domain.charge import Charge


class ChargeLedger(Protocol):
    def append(self, charges: tuple[Charge, ...]) -> None: ...
