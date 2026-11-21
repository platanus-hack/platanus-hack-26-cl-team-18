"""Per-screen stock flags set from `/clerk` (R-C4): a campaign eligibility fact. A stock-out re-auctions at once."""

from __future__ import annotations

from threading import Lock

from app.modules.campaigns.domain.book import BrandId


class StockBoard:
    """Which brands are out of stock at each screen. In memory; the clerk is the source."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._out: dict[str, frozenset[BrandId]] = {}

    def set(self, screen: str, brand: BrandId, *, in_stock: bool) -> bool:
        """Apply a toggle. Returns whether anything changed (same value twice is a no-op)."""
        with self._lock:
            current = self._out.get(screen, frozenset())
            updated = current - {brand} if in_stock else current | {brand}
            if updated == current:
                return False
            self._out[screen] = updated
            return True

    def out_of_stock(self, screen: str) -> frozenset[BrandId]:
        with self._lock:
            return self._out.get(screen, frozenset())
