"""Durable playback facts, with startup hydration for reconnecting consoles."""

from typing import TYPE_CHECKING, Protocol

from app.contract.messages import ConsoleReceipt

if TYPE_CHECKING:
    from app.modules.delivery.application.billing import Receipt


class ReceiptHistory(Protocol):
    def append(self, receipt: Receipt) -> None: ...

    def latest(self, screen_id: str) -> ConsoleReceipt | None: ...
