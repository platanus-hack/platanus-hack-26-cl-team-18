"""Issued offer facts survive a station restart; reads happen at startup only."""

from typing import Protocol

from app.modules.delivery.domain.redemption import BasketOffer


class OfferStore(Protocol):
    def append(self, offer: BasketOffer) -> None: ...

    def offers(self) -> tuple[BasketOffer, ...]: ...
