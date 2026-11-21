"""Playback report → attention-seconds → charges → moment receipt (delivery's use case).

Idempotent by the report's natural key (screen, creative, seq): a retried
report bills once, and the same key with a different window fails. Charges for
a play are computed from the prices of the auction that composed the creative,
never from a later re-clear.
"""

from __future__ import annotations

import hashlib
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from threading import Lock
from typing import Any

from app.modules.campaigns.domain.book import CampaignId
from app.modules.common.application.id_generator import IdGenerator
from app.modules.common.domain.primitives.errors import DomainError
from app.modules.common.domain.value_objects.money import Money
from app.modules.delivery.application.ports.ledger import ChargeLedger
from app.modules.delivery.domain.attention import (
    Attention,
    LookSample,
    measure_attention,
    seconds_of,
)
from app.modules.delivery.domain.charge import BilledSlot, Charge, ChargeId, price_play

# Attention samples older than this are useless for any play still reporting.
SAMPLE_RETENTION = timedelta(minutes=10)
MAX_PLAY = timedelta(minutes=10)
MAX_PLAYS_REMEMBERED = 500


class ConflictingPlaybackReport(DomainError):
    def __init__(self, **_: Any) -> None:
        super().__init__(
            code="delivery.conflicting_playback_report",
            msg="This playback report key was already billed with a different window.",
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class PlayRecord:
    """A paid creative that went on air, with the facts its bill needs."""

    creative_id: str
    auction_id: str
    screen_id: str
    people: int
    looking: int
    slots: tuple[BilledSlot, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class Receipt:
    play: PlayRecord
    played_s: Decimal
    attention: Attention
    charges: tuple[Charge, ...]

    @property
    def total(self) -> Money:
        currency = self.play.slots[0].price_per_as.currency
        return sum((charge.amount for charge in self.charges), Money.zero(currency=currency))

    def by_campaign(self) -> dict[CampaignId, Money]:
        return {charge.campaign_id: charge.amount for charge in self.charges}


class AttentionLog:
    """Recent looking samples per screen, in memory only (anonymous ids, short-lived)."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._samples: dict[str, deque[LookSample]] = {}

    def record(self, screen_id: str, at: datetime, looking: Iterable[str]) -> None:
        with self._lock:
            samples = self._samples.setdefault(screen_id, deque())
            samples.append(LookSample(at=at, looking=frozenset(looking)))
            while samples and samples[0].at < at - SAMPLE_RETENTION:
                samples.popleft()

    def samples(self, screen_id: str) -> tuple[LookSample, ...]:
        with self._lock:
            return tuple(self._samples.get(screen_id, ()))


class DeliveryDesk:
    def __init__(self, *, ledger: ChargeLedger, ids: IdGenerator) -> None:
        self._ledger = ledger
        self._ids = ids
        self._lock = Lock()
        self._plays: dict[str, PlayRecord] = {}
        self._billed: dict[str, tuple[str, Receipt]] = {}
        self.attention = AttentionLog()

    def aired(self, play: PlayRecord) -> None:
        with self._lock:
            self._plays[play.creative_id] = play
            while len(self._plays) > MAX_PLAYS_REMEMBERED:
                self._plays.pop(next(iter(self._plays)))

    def record_playback(
        self,
        *,
        screen_id: str,
        creative_id: str,
        seq: int,
        started_at: datetime,
        ended_at: datetime,
        recorded_at: datetime,
    ) -> Receipt | None:
        """Bill one finished play window. None for an unpaid creative (house ad) or unknown."""
        if ended_at < started_at or ended_at - started_at > MAX_PLAY:
            return None  # not a plausible window: bill nothing rather than guess
        key = f"{screen_id}:{creative_id}:{seq}"
        fingerprint = hashlib.sha256(
            f"{started_at.isoformat()}|{ended_at.isoformat()}".encode()
        ).hexdigest()
        with self._lock:
            if (seen := self._billed.get(key)) is not None:
                if seen[0] != fingerprint:
                    raise ConflictingPlaybackReport()
                return None  # a retry: already billed once
            play = self._plays.get(creative_id)
            if play is None or play.screen_id != screen_id:
                return None
            attention = measure_attention(
                self.attention.samples(screen_id), start=started_at, end=ended_at
            )
            charges = tuple(
                Charge(
                    charge_id=ChargeId.create(self._ids.new_uuid7()),
                    dedupe_key=key,
                    auction_id=play.auction_id,
                    creative_id=creative_id,
                    screen_id=screen_id,
                    campaign_id=priced.slot.campaign_id,
                    brand_id=priced.slot.brand_id,
                    attention_seconds=attention.attention_seconds,
                    price_per_as=priced.slot.price_per_as,
                    amount=priced.amount,
                    reason=priced.reason,
                    played_from=started_at,
                    played_to=ended_at,
                    recorded_at=recorded_at,
                )
                for priced in price_play(play.slots, attention.attention_seconds)
            )
            played = ended_at - started_at
            receipt = Receipt(
                play=play,
                played_s=seconds_of(played).quantize(Decimal("0.001")),
                attention=attention,
                charges=charges,
            )
            self._billed[key] = (fingerprint, receipt)
            while len(self._billed) > MAX_PLAYS_REMEMBERED * 4:
                self._billed.pop(next(iter(self._billed)))
        self._ledger.append(charges)
        return receipt
