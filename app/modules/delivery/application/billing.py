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
from app.modules.delivery.application.ports.budget import BudgetGate
from app.modules.delivery.application.ports.ledger import ChargeLedger
from app.modules.delivery.domain.attention import (
    LookSample,
    seconds_of,
)
from app.modules.delivery.domain.charge import (
    BilledSlot,
    Charge,
    ChargeId,
    ChargeReason,
    price_play,
)
from app.modules.delivery.domain.validity import ValidatedAttention, validate_attention

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
    aired_at: datetime  # server instant it went on air: no play is billed before it


@dataclass(frozen=True, slots=True, kw_only=True)
class Receipt:
    play: PlayRecord
    played_s: Decimal
    attention: ValidatedAttention
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

    def record(
        self,
        screen_id: str,
        at: datetime,
        looking: Iterable[str],
        far: Iterable[str] = (),
    ) -> None:
        with self._lock:
            samples = self._samples.setdefault(screen_id, deque())
            samples.append(LookSample(at=at, looking=frozenset(looking), far=frozenset(far)))
            while samples and samples[0].at < at - SAMPLE_RETENTION:
                samples.popleft()

    def samples(self, screen_id: str) -> tuple[LookSample, ...]:
        with self._lock:
            return tuple(self._samples.get(screen_id, ()))


class DeliveryDesk:
    def __init__(
        self, *, ledger: ChargeLedger, ids: IdGenerator, budgets: BudgetGate | None = None
    ) -> None:
        self._ledger = ledger
        self._ids = ids
        self._budgets = budgets
        self._lock = Lock()
        self._plays: dict[str, PlayRecord] = {}
        self._billed: dict[str, tuple[str, Receipt]] = {}
        self._unwritten: set[str] = set()
        # Per creative, the end of the last billed window: a play second bills once.
        self._billed_until: dict[str, datetime] = {}
        # Per creative, the server instant it left the screen: no later second bills.
        self._off_air: dict[str, datetime] = {}
        self.attention = AttentionLog()

    def aired(self, play: PlayRecord) -> None:
        with self._lock:
            self._end_plays(play.screen_id, play.aired_at)
            # Re-aired under the same creative id: move it to the newest end, so the
            # eviction below never forgets a play that is on air right now.
            self._plays.pop(play.creative_id, None)
            self._plays[play.creative_id] = play
            while len(self._plays) > MAX_PLAYS_REMEMBERED:
                evicted = self._plays.pop(next(iter(self._plays)))
                # A forgotten play never bills again (unknown creative), so its
                # per-creative marks are dead weight in a long-running process.
                self._off_air.pop(evicted.creative_id, None)
                self._billed_until.pop(evicted.creative_id, None)

    def off_air(self, screen_id: str, *, at: datetime) -> None:
        """Whatever paid play was on `screen_id` left it at `at` (another creative aired).

        The player reports a window only when a play ends, from its own clock: a late
        or clock-ahead report must not bill seconds the next creative was on air.
        """
        with self._lock:
            self._end_plays(screen_id, at)

    def _release_unbillable(self, screen_id: str, creative_id: str) -> None:
        """A report that bills nothing for a play already off air still ends its hold.

        Otherwise the worst-case hold outlives the play and shrinks the brand's
        budget until some later reservation happens to sweep it.
        """
        play = self._plays.get(creative_id)
        if (
            self._budgets is None
            or play is None
            or play.screen_id != screen_id
            or creative_id not in self._off_air
        ):
            return
        # Settling with $0 drops the hold and books nothing (no ledger row).
        self._budgets.charge_many(
            creative_id,
            {
                slot.campaign_id: Money.zero(currency=slot.price_per_as.currency)
                for slot in play.slots
            },
        )

    def _end_plays(self, screen_id: str, at: datetime) -> None:
        for creative_id, play in self._plays.items():
            if play.screen_id == screen_id and creative_id not in self._off_air:
                self._off_air[creative_id] = max(at, play.aired_at)

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
            # Not a plausible window: bill nothing rather than guess.
            with self._lock:
                self._release_unbillable(screen_id, creative_id)
            return None
        key = f"{screen_id}:{creative_id}:{seq}"
        fingerprint = hashlib.sha256(
            f"{started_at.isoformat()}|{ended_at.isoformat()}".encode()
        ).hexdigest()
        # Player clocks are untrusted. Never charge time the server has not
        # observed, or let a future window suppress later legitimate reports.
        ended_at = min(ended_at, recorded_at)
        with self._lock:
            if (seen := self._billed.get(key)) is not None:
                if seen[0] != fingerprint:
                    raise ConflictingPlaybackReport()
                if key in self._unwritten:
                    self._ledger.append(seen[1].charges)
                    self._unwritten.remove(key)
                    return seen[1]
                return None  # a retry: already billed once
            play = self._plays.get(creative_id)
            if play is None or play.screen_id != screen_id:
                return None
            # Bill only time this creative was on air and not already billed: a window
            # from before it aired, after it was replaced, or re-sent under a new seq,
            # never bills twice.
            if (left_at := self._off_air.get(creative_id)) is not None:
                ended_at = min(ended_at, left_at)
            start = max(started_at, play.aired_at, self._billed_until.get(creative_id, started_at))
            if start >= ended_at:
                self._release_unbillable(screen_id, creative_id)
                return None
            # Invalid-attention filtering runs before any money is computed.
            attention = validate_attention(
                self.attention.samples(screen_id), start=start, end=ended_at
            )
            priced_slots = price_play(play.slots, attention.attention_seconds)
            charge_ids = tuple(ChargeId.create(self._ids.new_uuid7()) for _ in priced_slots)
            amounts = {priced.slot.campaign_id: priced.amount for priced in priced_slots}
            booked_amounts = (
                amounts
                if self._budgets is None
                else self._budgets.charge_many(creative_id, amounts)
            )
            charges = tuple(
                Charge(
                    charge_id=charge_id,
                    dedupe_key=key,
                    auction_id=play.auction_id,
                    creative_id=creative_id,
                    screen_id=screen_id,
                    campaign_id=priced.slot.campaign_id,
                    brand_id=priced.slot.brand_id,
                    attention_seconds=attention.attention_seconds,
                    price_per_as=priced.slot.price_per_as,
                    amount=booked,
                    reason=ChargeReason.BUDGET_CAP if booked < priced.amount else priced.reason,
                    played_from=start,
                    played_to=ended_at,
                    recorded_at=recorded_at,
                )
                for charge_id, priced in zip(charge_ids, priced_slots, strict=True)
                for booked in (booked_amounts[priced.slot.campaign_id],)
            )
            played = ended_at - start
            receipt = Receipt(
                play=play,
                played_s=seconds_of(played).quantize(Decimal("0.001")),
                attention=attention,
                charges=charges,
            )
            self._billed[key] = (fingerprint, receipt)
            self._billed_until[creative_id] = ended_at
            self._unwritten.add(key)
            self._ledger.append(charges)
            self._unwritten.remove(key)
            while len(self._billed) > MAX_PLAYS_REMEMBERED * 4:
                oldest = next((key for key in self._billed if key not in self._unwritten), None)
                if oldest is None:
                    break
                self._billed.pop(oldest)
        return receipt
