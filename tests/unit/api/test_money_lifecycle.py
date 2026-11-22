"""The money lifecycle across components, over long randomized sessions.

Real wiring (build_container → register_live_market → register_demo) with a
fixed clock and a manual scheduler; the hub is captured instead of sockets so a
session of hundreds of steps runs fast. After EVERY step an invariant checker
compares the ledger, the console's receipts and book, the budget holds, what
aired and the QR redemptions against an independent record of the session.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

import app.bootstrap.live as live_module
from app.bootstrap.container import build_container
from app.bootstrap.demo import LiveMarket
from app.config.settings import AppSettings
from app.contract.messages import (
    AdvertiserBidMultiplier,
    AudienceSnapshot,
    ConsoleReceipt,
    CreativeReady,
    DetectedObject,
    DistanceBand,
    LiveSet,
    MinorLikely,
    PlaybackReport,
    PlayerDisplayed,
    SoundScene,
    StockSet,
    Tracklet,
)
from app.modules.campaigns.application.live_book import RESERVATION_TTL, LiveCampaignBook
from app.modules.campaigns.domain.book import CampaignId
from app.modules.common.domain.value_objects.money import Money
from app.modules.common.infrastructure.clock.fixed_clock import FixedClock
from app.modules.delivery.domain.redemption import (
    REDEMPTION_TTL,
    ExpiredRedemptionCode,
    UnknownRedemptionCode,
)
from app.realtime.router import Received

from .test_auction_loop_e2e import ManualScheduler

T0 = datetime(2026, 11, 21, 15, tzinfo=UTC)
SCREEN = "demo-01"
SCENES = ("fitness", "co-brand", "co-brand", "adult", "minor-safe", "empty", "coffee", "fitness")


@dataclass
class Aired:
    creative: CreativeReady
    on_at: datetime  # server instant the screen was told to show it
    off_at: datetime | None = None  # server instant a replacement was sent
    campaigns: frozenset[CampaignId] = frozenset()


@dataclass
class Session:
    clock: FixedClock
    scheduler: ManualScheduler
    container: Any
    market: LiveMarket
    rng: random.Random
    screen: list[CreativeReady] = field(default_factory=list)
    console: list[Any] = field(default_factory=list)
    aired: dict[str, Aired] = field(default_factory=dict)
    on_air: str | None = None
    seq: int = 0
    # Player-side: when the creative on screen started, and reports not yet delivered.
    shown_since: datetime | None = None
    report_seq: int = 0
    outbox: list[PlaybackReport] = field(default_factory=list)
    delivered: list[PlaybackReport] = field(default_factory=list)
    # Ground truth: (server instant, looking tracklet ids) for each fresh reading.
    looks: list[tuple[datetime, frozenset[str]]] = field(default_factory=list)
    live: bool = True
    multiplier_seq: int = 0
    redeemed: dict[tuple[str, str], frozenset[CampaignId]] = field(default_factory=dict)
    guard_rejects: bool = False
    people_pool: int = 0
    steps: list[str] = field(default_factory=list)

    # ------------------------------------------------------------ plumbing

    @property
    def book(self) -> LiveCampaignBook:
        return self.market.book

    def campaign_of_brand(self) -> dict[str, CampaignId]:
        return {str(e.bidder.brand_id): e.bidder.campaign_id for e in self.book.entries()}

    def received(self) -> Received:
        return Received(received_at=self.clock.now_utc())

    def dispatch(self, message: Any) -> None:
        self.container.router.dispatch_sync(message, self.received())
        self.absorb()

    def absorb(self) -> None:
        """Play what the screen was sent, like the player would (report the one replaced)."""
        while self.screen:
            creative = self.screen.pop(0)
            now = self.clock.now_utc()
            if self.on_air == creative.creative_id:
                continue
            if self.on_air is not None:
                self.aired[self.on_air].off_at = now
                self.finish_play(self.on_air, now)
            brands = self.campaign_of_brand()
            paid = creative.auction_id is not None
            self.aired[creative.creative_id] = Aired(
                creative=creative,
                on_at=now,
                campaigns=frozenset(brands[s.brand_id] for s in creative.spec.slots)
                if paid
                else frozenset(),
            )
            self.on_air = creative.creative_id
            self.shown_since = now
            if self.rng.random() < 0.7:
                self.container.router.dispatch_sync(
                    PlayerDisplayed(
                        screen_id=SCREEN,
                        creative_id=creative.creative_id,
                        seq=1,
                        displayed_at=now,
                    ),
                    self.received(),
                )

    def finish_play(self, creative_id: str, now: datetime) -> None:
        assert self.shown_since is not None
        self.report_seq += 1
        end = now
        if self.rng.random() < 0.15:
            end = now + timedelta(seconds=self.rng.uniform(1, 120))  # player clock ahead
        start = self.shown_since
        if self.rng.random() < 0.1:
            start = start - timedelta(seconds=self.rng.uniform(1, 60))  # claims earlier start
        report = PlaybackReport(
            screen_id=SCREEN,
            creative_id=creative_id,
            seq=self.report_seq,
            started_at=start,
            ended_at=max(end, start),
        )
        roll = self.rng.random()
        if roll < 0.05:
            return  # lost: the player closed before it sent
        self.outbox.append(report)
        if roll < 0.15:
            self.outbox.append(report)  # duplicate
        elif roll < 0.2:
            self.report_seq += 1  # the same window re-sent under a new seq
            self.outbox.append(report.model_copy(update={"seq": self.report_seq}))

    # ------------------------------------------------------------ actions

    def snapshot(self, scene: str, looking: int) -> None:
        self.seq += 1
        now = self.clock.now_utc()
        ids = [f"p{(self.people_pool + i) % 6}" for i in range(looking)]
        if scene == "fitness" or scene == "co-brand":
            objects = [DetectedObject(label="sports ball", score=1)]
        elif scene in {"adult", "minor-safe"}:
            objects = [DetectedObject(label="tap handle fixture", score=1)]
        elif scene == "coffee":
            objects = [DetectedObject(label="cup", score=1)] * 3
        else:
            objects = []
        if scene == "empty":
            ids = []
        snapshot = AudienceSnapshot(
            screen_id=SCREEN,
            seq=self.seq,
            measured_at=now,
            people=len(ids),
            looking=len(ids),
            tracklets=[
                Tracklet(id=i, dwell_s=1.0, looking=True, distance_band=DistanceBand.NEAR)
                for i in ids
            ],
            objects=objects if ids else [],
            sound_scene=(
                [SoundScene(label="coffee service fixture", score=1)]
                if scene == "co-brand" and ids
                else []
            ),
            minor_likely=MinorLikely(value=scene == "minor-safe", confidence=1),
        )
        if not self.live:
            return  # the gateway drops agent readings while sensing is off
        self.looks.append((now, frozenset(ids)))
        self.dispatch(snapshot)

    def step(self) -> None:
        rng = self.rng
        self.clock.advance(timedelta(seconds=rng.choice((0.1, 0.5, 1, 2, 5, 9, 16, 31))))
        self.scheduler.fire_due()
        self.absorb()
        roll = rng.random()
        if roll < 0.45:
            if rng.random() < 0.1:
                self.people_pool += rng.randint(1, 3)
            scene = rng.choice(SCENES)
            looking = rng.choice((1, 1, 2, 2, 3, 4))
            self.steps.append(f"snapshot {scene} x{looking}")
            self.snapshot(scene, looking)
        elif roll < 0.6:
            self.deliver_reports()
        elif roll < 0.68:
            entries = self.book.entries()
            entry = rng.choice(entries)
            self.multiplier_seq += 1
            seq = self.multiplier_seq
            if rng.random() < 0.1:
                seq = max(1, seq - 3)  # a late, stale slider move
            value = rng.choice(("0.2", "0.5", "1", "1.5", "3", "5"))
            self.steps.append(f"multiplier {value}")
            try:
                self.container.demo.set_multiplier(
                    AdvertiserBidMultiplier(
                        campaign_id=str(entry.bidder.campaign_id), multiplier=value, seq=seq
                    )
                )
            except Exception as error:  # a conflicting seq is refused, never applied
                assert type(error).__name__ == "ConflictingMultiplierSeq"
            self.absorb()
        elif roll < 0.74:
            entry = rng.choice(self.book.entries())
            in_stock = rng.random() < 0.5
            self.steps.append(f"stock {in_stock}")
            self.container.demo.set_stock(
                StockSet(screen_id=SCREEN, brand_id=str(entry.bidder.brand_id), in_stock=in_stock)
            )
            self.absorb()
        elif roll < 0.79:
            self.live = not self.live
            self.steps.append(f"live {self.live}")
            self.container.live.set(LiveSet(screen_id=SCREEN, live=self.live))
            self.absorb()
        elif roll < 0.83:
            self.guard_rejects = not self.guard_rejects
            self.steps.append(f"guard {self.guard_rejects}")
        else:
            self.redeem()

    def deliver_reports(self) -> None:
        if not self.outbox:
            return
        self.rng.shuffle(self.outbox)  # out of order
        count = self.rng.randint(1, len(self.outbox))
        batch, self.outbox = self.outbox[:count], self.outbox[count:]
        for report in batch:
            self.steps.append(f"report {report.creative_id[-6:]} seq={report.seq}")
            self.delivered.append(report)
            self.dispatch(report)

    def redeem(self) -> None:
        offers = [a for a in self.aired.values() if a.creative.spec.redemption_code]
        if not offers:
            return
        aired = self.rng.choice(offers)
        code = aired.creative.spec.redemption_code
        assert code is not None
        nonce = f"phone{self.rng.randint(0, 3)}abc"
        now = self.clock.now_utc()
        self.steps.append(f"redeem {code}")
        try:
            _, created = self.container.demo.redeem(code, nonce)
        except ExpiredRedemptionCode:
            assert now >= aired.on_at + REDEMPTION_TTL - timedelta(seconds=1)
            return
        except UnknownRedemptionCode:
            pytest.fail(f"an issued code {code} is unknown")
        offer = self.container.demo.offer_for(code)
        assert offer is not None
        # A new sale only within its window (a re-scan later is the same sale),
        # credited to exactly that QR's basket.
        if created:
            assert offer.issued_at <= now < offer.expires_at
        assert frozenset(offer.campaign_ids) == aired.campaigns
        assert offer.creative_id == aired.creative.creative_id
        key = (code, nonce)
        assert created == (key not in self.redeemed), "one sale per phone per code"
        self.redeemed[key] = frozenset(offer.campaign_ids)

    def quiesce(self) -> None:
        """Room empties, every report lands, the grace and every lease pass."""
        if not self.live:
            self.live = True
            self.container.live.set(LiveSet(screen_id=SCREEN, live=True))
            self.absorb()
        for _ in range(3):
            self.clock.advance(timedelta(seconds=2))
            self.snapshot("empty", 0)
            self.scheduler.fire_due()
            self.absorb()
        self.clock.advance(timedelta(seconds=30))
        self.scheduler.fire_due()
        self.absorb()
        self.deliver_all()
        self.clock.advance(RESERVATION_TTL + timedelta(seconds=30))
        self.scheduler.fire_due()
        self.absorb()
        self.deliver_all()

    def deliver_all(self) -> None:
        while self.outbox:
            report = self.outbox.pop(0)
            self.delivered.append(report)
            self.dispatch(report)


# ---------------------------------------------------------------- invariants


def check(session: Session, *, restored: dict[CampaignId, Money] | None = None) -> None:
    charges = session.container.ledger.charges()
    entries = {e.bidder.campaign_id: e for e in session.book.entries()}
    view = {line.campaign_id: line for line in session.container.demo.book_view().lines}
    receipts = [m for m in session.console if isinstance(m, ConsoleReceipt)]
    by_receipt: dict[str, Decimal] = {}
    for receipt in receipts:
        for line in receipt.charges:
            by_receipt[line.campaign_id] = by_receipt.get(line.campaign_id, Decimal(0)) + Decimal(
                line.amount.amount
            )
    ledger: dict[CampaignId, Decimal] = {}
    for charge in charges:
        assert charge.amount.amount >= 0
        assert charge.amount.currency == "CLP"
        assert charge.amount.amount == charge.amount.amount.to_integral_value(), charge
        ledger[charge.campaign_id] = ledger.get(charge.campaign_id, Decimal(0)) + (
            charge.amount.amount
        )
        # Zero attention costs exactly zero.
        if charge.attention_seconds == 0:
            assert charge.amount.amount == 0
        # Never more than the cleared price x attention (rounded to the peso).
        assert (
            charge.amount.amount
            <= (charge.price_per_as.amount * charge.attention_seconds).to_integral_value(
                rounding="ROUND_HALF_EVEN"
            )
            + 0
        ), charge
        # Billed only for that brand's own creative, inside the time it was on air.
        aired = session.aired.get(charge.creative_id)
        assert aired is not None, "charged a creative the screen never showed"
        assert charge.campaign_id in aired.campaigns, "charged a brand not in that creative"
        assert charge.played_from >= aired.on_at, (charge.played_from, aired.on_at)
        if aired.off_at is not None:
            assert charge.played_to <= aired.off_at, (
                "billed time after the creative was replaced",
                charge.played_to,
                aired.off_at,
                session.steps[-12:],
            )
    # Attention never exceeds looking viewers x wall time on air.
    attention: dict[str, Decimal] = {}
    for charge in {(c.creative_id, c.dedupe_key): c for c in charges}.values():
        attention[charge.creative_id] = attention.get(charge.creative_id, Decimal(0)) + (
            charge.attention_seconds
        )
    for creative_id, seconds in attention.items():
        aired = session.aired[creative_id]
        end = aired.off_at or session.clock.now_utc()
        assert seconds <= Decimal(looking_seconds(session.looks, aired.on_at, end)) + Decimal(
            "0.001"
        ), (creative_id, seconds)
    for campaign_id, entry in entries.items():
        account = entry.account
        assert account is not None
        base = (restored or {}).get(campaign_id, Money.zero(currency="CLP")).amount
        spent = ledger.get(campaign_id, Decimal(0))
        assert account.spent.amount == base + spent, (campaign_id, account.spent, spent)
        line = view[str(campaign_id)]
        assert Decimal(line.spent.amount) == account.spent.amount
        assert Decimal(by_receipt.get(str(campaign_id), Decimal(0))) == spent
        assert account.spent <= account.budget, "overspend"
        assert account.remaining.amount >= 0
        assert account.reserved.amount >= 0
        # A hold is only ever for a creative that is on air or still owes its report.
        owed = {r.creative_id for r in session.outbox}
        for reservation in account.reservations:
            assert reservation.key in session.aired, reservation
            assert campaign_id in session.aired[reservation.key].campaigns
            assert (
                reservation.key == session.on_air
                or reservation.key in owed
                or (
                    session.clock.now_utc() - reservation.at
                    <= RESERVATION_TTL + timedelta(seconds=31)
                )
            ), (
                "leaked hold",
                reservation,
                session.aired[reservation.key].on_at,
                session.aired[reservation.key].off_at,
                [r for r in session.delivered if r.creative_id == reservation.key],
            )
    # The screen is never blank.
    assert session.on_air is not None
    assert session.aired[session.on_air].creative.spec.slots
    # Redemptions credit exactly that QR's basket, once per phone.
    for (code, _), campaigns in session.redeemed.items():
        offer = session.container.demo.offer_for(code)
        assert offer is not None and frozenset(offer.campaign_ids) == campaigns


def looking_seconds(
    looks: list[tuple[datetime, frozenset[str]]], start: datetime, end: datetime
) -> float:
    """Upper bound: each reading's lookers, held until the next reading (max 10 s)."""
    total = 0.0
    ordered = sorted(looks)
    for index, (at, ids) in enumerate(ordered):
        following = ordered[index + 1][0] if index + 1 < len(ordered) else end
        seg_start, seg_end = max(at, start), min(following, at + timedelta(seconds=10), end)
        if seg_end > seg_start:
            total += len(ids) * (seg_end - seg_start).total_seconds()
    return total


# ---------------------------------------------------------------- rig


@pytest.fixture
def make_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Callable[..., Session]]:
    from app.modules.creative.application.studio import CreativeStudio
    from app.modules.creative.domain.guard import GuardVerdict, Violation, ViolationKind

    holder: dict[str, Session] = {}
    real_compose = CreativeStudio.compose

    def compose(self: CreativeStudio, brief: Any, *, variant: int = 0) -> Any:
        composed = real_compose(self, brief, variant=variant)
        session = holder.get("s")
        if session is not None and session.guard_rejects:
            unsafe = GuardVerdict(
                violations=(Violation(kind=ViolationKind.COMPETITOR_NAME, where="offer"),)
            )
            return replace(composed, score=replace(composed.score, verdict=unsafe))
        return composed

    monkeypatch.setattr(CreativeStudio, "compose", compose)

    def make(seed: int, *, budget_clp: int = 400, swap_holds: bool = True) -> Session:
        markets: list[LiveMarket] = []
        real_seed = live_module.seed_market

        def capture(**kwargs: Any) -> LiveMarket:
            market = real_seed(**kwargs)
            markets.append(market)
            return market

        monkeypatch.setattr(live_module, "seed_market", capture)
        clock = FixedClock(T0)
        scheduler = ManualScheduler(clock)
        settings = AppSettings(
            app_env="test",
            demo_simulator=True,
            media_dir=tmp_path,
            auction_swap_holds=swap_holds,
            demo_budget_clp=budget_clp,
        )
        container = build_container(settings, clock=clock, scheduler=scheduler)
        session = Session(
            clock=clock,
            scheduler=scheduler,
            container=container,
            market=markets[0],
            rng=random.Random(seed),
        )
        hub = container.hub
        hub.send_to_screen_threadsafe = lambda message, sink=None: (
            session.screen.append(message) if isinstance(message, CreativeReady) else None
        )
        hub.broadcast_console_threadsafe = lambda *messages, sink=None: session.console.extend(
            messages
        )
        hub.broadcast_station_threadsafe = lambda *messages, **_: None
        hub.broadcast_live_threadsafe = lambda *messages, **_: None
        hub.send_to_agents_threadsafe = lambda *messages, **_: None
        # The player connects and is sent what is on air (the house ad).
        container.router.player_connected_sync(SCREEN, object())
        session.absorb()
        container.live.set(LiveSet(screen_id=SCREEN, live=True))
        session.absorb()
        holder["s"] = session
        return session

    yield make


@pytest.mark.parametrize("seed", range(12))
def test_a_long_randomized_session_keeps_every_money_invariant(
    make_session: Callable[..., Session], seed: int
) -> None:
    session = make_session(seed, budget_clp=random.Random(seed).choice((150, 250, 400, 5_000)))
    assert session.on_air is not None  # the house ad before anyone arrives
    for _ in range(300):
        session.step()
        check(session)
    session.quiesce()
    check(session)
    # Every hold was settled or released: available is budget minus spent.
    for entry in session.book.entries():
        account = entry.account
        assert account is not None
        assert account.reserved.amount == 0, (account.reservations, session.steps[-20:])
        assert account.remaining == account.budget - account.spent or account.remaining.amount == 0


class _FakeCampaignDb:
    """Answers the registry's three statements from rows (the Postgres record)."""

    def __init__(self, rows: dict[str, tuple[Any, ...]], spent: dict[str, Decimal]) -> None:
        self.rows = rows
        self.spent = spent
        self._result: list[tuple[Any, ...]] = []

    def __call__(self) -> _FakeCampaignDb:
        return self

    def __enter__(self) -> _FakeCampaignDb:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def cursor(self) -> _FakeCampaignDb:
        return self

    def close(self) -> None:
        return None

    def execute(self, sql: str, params: tuple[Any, ...]) -> None:
        if sql.lstrip().startswith("INSERT INTO campaign"):
            campaign_id, brand_id, slug, currency, budget, *_ = params
            self.rows.setdefault(slug, (slug, campaign_id, brand_id, currency, budget, "1", 0))
        elif "FROM campaign" in sql:
            self._result = [self.rows[slug] for slug in params[0] if slug in self.rows]
        else:  # the spend, summed from the charge ledger
            self._result = [
                (campaign_id, "CLP", amount)
                for campaign_id, amount in self.spent.items()
                if campaign_id in params[0]
            ]

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._result


def test_a_restart_restores_the_same_book_from_the_registry_and_ledger(
    make_session: Callable[..., Session],
) -> None:
    from app.bootstrap import demo_catalog
    from app.bootstrap.demo import seed_market
    from app.modules.campaigns.infrastructure.registry import PostgresCampaignRegistry
    from app.modules.common.application.id_generator import IdGenerator

    session = make_session(1, budget_clp=250)
    for _ in range(300):
        session.step()
        check(session)
    session.quiesce()
    spent: dict[str, Decimal] = {}
    for charge in session.container.ledger.charges():
        key = str(charge.campaign_id)
        spent[key] = spent.get(key, Decimal(0)) + charge.amount.amount
    entries = session.book.entries()
    rows = {
        brand.slug: (
            brand.slug,
            entry.bidder.campaign_id.value,
            entry.bidder.brand_id.value,
            "CLP ",  # a CHAR(3)-style padded currency column
            entry.account.budget.amount if entry.account else Decimal(0),
            str(entry.bidder.live_multiplier.value),
            entry.multiplier_seq,
        )
        for brand, entry in zip(demo_catalog.FICTIONAL.brands, entries, strict=True)
    }
    db = _FakeCampaignDb(rows, spent)
    registry = PostgresCampaignRegistry(
        db,
        writer=None,
        ids=IdGenerator(),
        clock=session.clock,  # type: ignore[arg-type]
    )
    restarted = seed_market(ids=IdGenerator(), budget_clp=99_999, registry=registry)
    before = {e.bidder.campaign_id: e for e in entries}
    for entry in restarted.book.entries():
        old = before[entry.bidder.campaign_id]
        assert entry.account is not None and old.account is not None
        assert entry.account.budget == old.account.budget  # the stored budget, not the env's
        assert entry.account.spent == old.account.spent  # spend derived from the ledger
        assert entry.account.remaining == old.account.remaining
        assert entry.account.reserved.amount == 0
        assert entry.bidder.live_multiplier == old.bidder.live_multiplier
        assert entry.multiplier_seq == old.multiplier_seq
    assert restarted.book.snapshot() == session.book.snapshot()


def test_budget_exhausted_mid_play_takes_the_play_down_without_overspend(
    make_session: Callable[..., Session],
) -> None:
    session = make_session(0, budget_clp=60)
    session.clock.advance(timedelta(seconds=1))
    session.snapshot("fitness", 1)
    paid = session.aired[session.on_air or ""]
    assert paid.campaigns, "a paid brand wins the lone shopper"
    (campaign,) = paid.campaigns
    while session.on_air == paid.creative.creative_id:
        assert session.clock.now_utc() - paid.on_at < timedelta(seconds=60), "never came down"
        session.clock.advance(timedelta(seconds=5))
        session.snapshot("fitness", 1)  # still looking: only the budget ends this play
        session.scheduler.fire_due()
        session.absorb()
        check(session)
    # The next lease could not be covered: the play came down at a lease boundary,
    # and the screen shows something else (never blank).
    assert paid.off_at is not None
    assert session.aired[session.on_air or ""].creative.spec.slots
    session.deliver_all()
    check(session)
    (account,) = (e.account for e in session.book.entries() if e.bidder.campaign_id == campaign)
    assert account is not None
    assert 0 < account.spent.amount <= account.budget.amount
    assert all(r.key != paid.creative.creative_id for r in account.reservations)


@pytest.mark.parametrize("ending", ["replaced", "out_of_budget"])
def test_a_play_whose_report_never_comes_frees_its_hold_after_the_ttl(
    make_session: Callable[..., Session], ending: str
) -> None:
    # Money audit: holds only expired inside the next reserve(), so with the room
    # empty (house ad, no new paid play) a lost report pinned the worst case forever.
    session = make_session(0, budget_clp=60 if ending == "out_of_budget" else 5_000)
    session.clock.advance(timedelta(seconds=1))
    session.snapshot("fitness", 1)
    paid = session.aired[session.on_air or ""]
    assert paid.campaigns
    while session.on_air == paid.creative.creative_id:
        session.clock.advance(timedelta(seconds=5))
        session.snapshot("fitness" if ending == "out_of_budget" else "empty", 1)
        session.scheduler.fire_due()
        session.absorb()
    session.outbox.clear()  # the player closed: its report is lost
    for _ in range(30):
        session.clock.advance(timedelta(seconds=10))
        session.scheduler.fire_due()
        session.absorb()
    for entry in session.book.entries():
        assert entry.account is not None
        held = [r for r in entry.account.reservations if r.key == paid.creative.creative_id]
        assert not held, held
