"""Screen <-> backend wire contract (docs/architecture.md §10).

This module is the single source of truth for every WebSocket message. The TS
side (`web/src/contract/messages.gen.ts`, zod schemas + inferred types) is
generated from it: run `make contract` after any change, and commit both.
`tests/unit/contract/test_contract_codegen.py` fails CI when they drift.

Rules:
- Self-contained: imports only pydantic and the stdlib, so the perception agent
  (`edge/`) can vendor this file verbatim. Never import `app.*` here.
- Every message is versioned JSON: a `type` discriminator plus `v`. A breaking
  change bumps `v` for that message type; additive optional fields don't.
- Strict: unknown fields are rejected and models are frozen.
- Instants are timezone-aware datetimes; name the instant (`measured_at` is the
  device's clock, `received_at` the backend's).
- Money is never a float: `MoneyDTO.amount` is a decimal string.
- No identity, ever: tracklet ids are anonymous and session-local, and
  utterance text is ephemeral (never persisted, never logged).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, TypeAdapter, model_validator

CONTRACT_VERSION = 1

ScreenId = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.:-]+$")]
EntityId = Annotated[str, Field(min_length=1, max_length=64)]
Seq = Annotated[int, Field(ge=0)]
Score = Annotated[float, Field(ge=0.0, le=1.0)]
# IAB Audience Taxonomy ID. The tagging adapter checks catalog membership.
SegmentId = Annotated[str, Field(min_length=1, max_length=32, pattern=r"^[A-Za-z0-9_-]+$")]


class _Model(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, json_schema_serialization_defaults_required=True
    )


class _Message(_Model):
    v: Literal[1] = 1


# ---------------------------------------------------------------- shared parts


class DistanceBand(StrEnum):
    NEAR = "near"
    MID = "mid"
    FAR = "far"


class UpgradeKind(StrEnum):
    COPY = "copy"
    VOICE = "voice"
    IMAGE = "image"
    VIDEO = "video"


class SignalSource(StrEnum):
    VISION = "vision"
    VLM = "vlm"
    SPEECH = "speech"
    SOUND = "sound"


class ExclusionReason(StrEnum):
    COMPETITOR = "competitor"
    SAFETY = "safety"
    BUDGET = "budget"
    BELOW_RESERVE = "below_reserve"
    NO_LAYOUT = "no_layout"
    NOT_PARTNER = "not_partner"
    OUT_OF_STOCK = "out_of_stock"
    INACTIVE = "inactive"
    NO_BASKET_OFFER = "no_basket_offer"
    OUTBID = "outbid"
    INVALID_BID = "invalid_bid"


class DegradationFlag(StrEnum):
    AGENT_OFFLINE = "agent_offline"
    VLM_UNAVAILABLE = "vlm_unavailable"
    COPY_FALLBACK = "copy_fallback"
    VOICE_FALLBACK = "voice_fallback"
    IMAGE_FALLBACK = "image_fallback"
    HOUSE_AD = "house_ad"


class MoneyDTO(_Model):
    amount: Annotated[str, Field(pattern=r"^-?\d+(\.\d+)?$", max_length=32)]
    currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")]


class Tracklet(_Model):
    id: Annotated[str, Field(min_length=1, max_length=32)]
    dwell_s: Annotated[float, Field(ge=0.0)]
    looking: bool
    distance_band: DistanceBand


class DetectedObject(_Model):
    label: Annotated[str, Field(min_length=1, max_length=64)]
    score: Score


class SoundScene(_Model):
    label: Annotated[str, Field(min_length=1, max_length=64)]
    score: Score


class MinorLikely(_Model):
    value: bool
    confidence: Score


class LatencyTrace(_Model):
    """Every decision carries one; add a field when you add a step to the loop."""

    measured_at: AwareDatetime | None = None
    received_at: AwareDatetime | None = None
    moment_changed_at: AwareDatetime | None = None
    cleared_at: AwareDatetime | None = None
    ready_at: AwareDatetime | None = None
    displayed_at: AwareDatetime | None = None
    upgraded_at: AwareDatetime | None = None
    # Minimum ad seconds: the earliest instant this decision may replace what plays.
    # The intentional hold is excluded from glass-to-glass latency.
    swap_not_before: AwareDatetime | None = None


class CreativeSlot(_Model):
    brand_id: EntityId
    brand_name: Annotated[str, Field(min_length=1, max_length=80)]
    logo_url: str  # the brand kit's real asset; never AI-generated
    copy_text: Annotated[str, Field(max_length=280)]
    accent_color: Annotated[str, Field(pattern=r"^#[0-9a-fA-F]{6}$")] | None = None
    hero_image_url: str | None = None


class CreativeSpec(_Model):
    layout_id: EntityId
    slots: Annotated[list[CreativeSlot], Field(min_length=1, max_length=3)]
    duration_ms: Annotated[int, Field(ge=1000, le=60_000)]
    video_url: str | None = None
    voiceover_url: str | None = None
    qr_url: str | None = None


# ---------------------------------------------------------------- agent -> backend


class AudienceSnapshot(_Message):
    type: Literal["audience.snapshot"] = "audience.snapshot"
    screen_id: ScreenId
    seq: Seq
    measured_at: AwareDatetime
    people: Annotated[int, Field(ge=0)]
    looking: Annotated[int, Field(ge=0)]
    tracklets: list[Tracklet] = Field(default_factory=list, max_length=64)
    objects: list[DetectedObject] = Field(default_factory=list, max_length=64)
    sound_scene: list[SoundScene] = Field(default_factory=list, max_length=16)
    minor_likely: MinorLikely

    @model_validator(mode="after")
    def _looking_within_people(self) -> AudienceSnapshot:
        if self.looking > self.people:
            raise ValueError("looking cannot exceed people")
        return self


class AudienceKeyframe(_Message):
    """Header for a face-blurred JPEG; the bytes follow as the next binary WS frame."""

    type: Literal["audience.keyframe"] = "audience.keyframe"
    screen_id: ScreenId
    seq: Seq
    measured_at: AwareDatetime
    content_type: Literal["image/jpeg"] = "image/jpeg"
    byte_length: Annotated[int, Field(gt=0, le=2_000_000)]


class AudienceUtterance(_Message):
    type: Literal["audience.utterance"] = "audience.utterance"
    screen_id: ScreenId
    seq: Seq
    measured_at: AwareDatetime
    text: Annotated[str, Field(max_length=1000)]  # ephemeral: never persisted or logged
    language: Annotated[str, Field(min_length=2, max_length=16)]
    keyterms_hit: list[str] = Field(default_factory=list, max_length=32)


# ---------------------------------------------------------------- backend -> player


class CreativeReady(_Message):
    type: Literal["creative.ready"] = "creative.ready"
    screen_id: ScreenId
    creative_id: EntityId
    auction_id: EntityId | None = None  # None for a house ad
    spec: CreativeSpec
    trace: LatencyTrace


class CreativeUpgraded(_Message):
    type: Literal["creative.upgraded"] = "creative.upgraded"
    screen_id: ScreenId
    creative_id: EntityId
    auction_id: EntityId | None = None
    upgrade_kind: UpgradeKind
    spec: CreativeSpec
    trace: LatencyTrace


# ---------------------------------------------------------------- player -> backend


class PlaybackReport(_Message):
    type: Literal["playback.report"] = "playback.report"
    screen_id: ScreenId
    creative_id: EntityId
    seq: Seq
    started_at: AwareDatetime
    ended_at: AwareDatetime

    @model_validator(mode="after")
    def _ends_after_start(self) -> PlaybackReport:
        if self.ended_at < self.started_at:
            raise ValueError("ended_at must not be before started_at")
        return self


# ---------------------------------------------------------------- backend -> console


class SegmentScore(_Model):
    segment_id: SegmentId
    label: Annotated[str, Field(max_length=80)]
    score: Score
    sources: list[SignalSource] = Field(default_factory=list)


class HeldProductScore(_Model):
    """A product someone holds (prop marker or label read): the object, never the person."""

    category: Annotated[str, Field(min_length=1, max_length=64)]
    brand_ref: Annotated[str, Field(min_length=1, max_length=64)] | None = None
    score: Score


class ConsoleMoment(_Message):
    type: Literal["console.moment"] = "console.moment"
    screen_id: ScreenId
    moment_id: EntityId
    people: Annotated[int, Field(ge=0)]
    looking: Annotated[int, Field(ge=0)]
    segments: list[SegmentScore]
    minor_likely: MinorLikely
    trace: LatencyTrace
    held_products: list[HeldProductScore] = Field(default_factory=list, max_length=16)


class BidLine(_Model):
    campaign_id: EntityId
    brand_name: Annotated[str, Field(max_length=80)]
    category: Annotated[str, Field(max_length=64)]
    bid_per_as: MoneyDTO  # per attention-second
    value: Annotated[float, Field(ge=0.0)]  # relevance-weighted score, not money
    value_per_s: MoneyDTO | None = None  # the bid's money value per screen-second
    multiplier: Annotated[str, Field(pattern=r"^\d+(\.\d+)?$", max_length=16)] | None = None


class Exclusion(_Model):
    campaign_id: EntityId
    reason: ExclusionReason
    detail: Annotated[str, Field(max_length=200)] = ""


class Winner(_Model):
    campaign_id: EntityId
    brand_name: Annotated[str, Field(max_length=80)]
    price_per_as: MoneyDTO
    payment_per_s: MoneyDTO | None = None


class ConsoleAuction(_Message):
    type: Literal["console.auction"] = "console.auction"
    screen_id: ScreenId
    auction_id: EntityId
    moment_id: EntityId
    bids: list[BidLine]
    exclusions: list[Exclusion]
    winners: list[Winner]  # empty = no fill (house ad)
    bundle_value: MoneyDTO
    single_brand_value: MoneyDTO  # the single-brand counterfactual
    coverage: Score  # est. share of looking viewers a winner matches (upper bound)
    trace: LatencyTrace
    # Revenue per screen-second, like-for-like with the single-brand counterfactual.
    bundle_revenue: MoneyDTO | None = None
    single_brand_revenue: MoneyDTO | None = None
    pricing: Annotated[str, Field(max_length=32)] | None = None
    trigger: Annotated[str, Field(max_length=32)] | None = None
    swap: Annotated[str, Field(max_length=32)] | None = (
        None  # swap | held | unchanged | keep_incumbent
    )


class ConsoleLatency(_Message):
    type: Literal["console.latency"] = "console.latency"
    screen_id: ScreenId
    creative_id: EntityId
    trace: LatencyTrace


class ConsoleDegradation(_Message):
    type: Literal["console.degradation"] = "console.degradation"
    screen_id: ScreenId
    flags: list[DegradationFlag]


# ---------------------------------------------------------------- operator inputs (HTTP bodies)
# `/advertiser` (judges' phones, through the tunnel) and `/clerk` (stock tablet)
# post these to /api/*. They are versioned contract messages so the TS side is typed.

Multiplier = Annotated[str, Field(pattern=r"^\d+(\.\d{1,2})?$", max_length=8)]


class AdvertiserBidMultiplier(_Message):
    """The bidding-war slider: bounded live multiplier, idempotent by (campaign_id, seq)."""

    type: Literal["advertiser.bid_multiplier"] = "advertiser.bid_multiplier"
    campaign_id: EntityId
    multiplier: Multiplier  # decimal string within [0.2, 5.0]
    seq: Seq


class StockSet(_Message):
    """A clerk's stock toggle for one brand at one screen. A stock-out re-auctions."""

    type: Literal["stock.set"] = "stock.set"
    screen_id: ScreenId
    brand_id: EntityId
    in_stock: bool


class BookLine(_Model):
    campaign_id: EntityId
    brand_id: EntityId
    brand_name: Annotated[str, Field(max_length=80)]
    category: Annotated[str, Field(max_length=64)]
    accent_color: Annotated[str, Field(pattern=r"^#[0-9a-fA-F]{6}$")] | None = None
    multiplier: Multiplier
    multiplier_seq: Seq
    in_stock: bool
    on_air: bool
    budget: MoneyDTO
    spent: MoneyDTO
    reserved: MoneyDTO
    remaining: MoneyDTO


class ConsoleBook(_Message):
    """The live campaign book for one screen: bids, stock and budgets (also GET /api/book)."""

    type: Literal["console.book"] = "console.book"
    screen_id: ScreenId
    lines: list[BookLine]


class ChargeReason(StrEnum):
    ATTENTION = "attention"
    NO_ATTENTION = "no_attention"


class ChargeLine(_Model):
    campaign_id: EntityId
    brand_name: Annotated[str, Field(max_length=80)]
    attention_seconds: Annotated[str, Field(pattern=r"^\d+(\.\d+)?$", max_length=16)]
    price_per_as: MoneyDTO
    amount: MoneyDTO
    reason: ChargeReason


class ConsoleReceipt(_Message):
    """The moment receipt: who looked, for how long, and what each brand paid."""

    type: Literal["console.receipt"] = "console.receipt"
    screen_id: ScreenId
    creative_id: EntityId
    auction_id: EntityId
    people: Annotated[int, Field(ge=0)]
    looking: Annotated[int, Field(ge=0)]
    played_s: Annotated[str, Field(pattern=r"^\d+(\.\d+)?$", max_length=16)]
    attention_seconds: Annotated[str, Field(pattern=r"^\d+(\.\d+)?$", max_length=16)]
    charges: list[ChargeLine]
    total: MoneyDTO


# ---------------------------------------------------------------- any direction


class ErrorMessage(_Message):
    """Sent back on the same socket when an inbound message is rejected."""

    type: Literal["system.error"] = "system.error"
    code: Annotated[str, Field(max_length=64)]
    message: Annotated[str, Field(max_length=500)]
    rejected_type: str | None = None


# ---------------------------------------------------------------- unions per socket

AgentInbound = Annotated[
    AudienceSnapshot | AudienceKeyframe | AudienceUtterance, Field(discriminator="type")
]
PlayerInbound = Annotated[PlaybackReport, Field(discriminator="type")]
PlayerOutbound = Annotated[
    CreativeReady | CreativeUpgraded | ErrorMessage, Field(discriminator="type")
]
ConsoleOutbound = Annotated[
    ConsoleMoment
    | ConsoleAuction
    | ConsoleLatency
    | ConsoleDegradation
    | CreativeReady
    | CreativeUpgraded
    | ErrorMessage
    | ConsoleBook
    | ConsoleReceipt,
    Field(discriminator="type"),
]

AGENT_INBOUND: TypeAdapter[AudienceSnapshot | AudienceKeyframe | AudienceUtterance] = TypeAdapter(
    AgentInbound
)
PLAYER_INBOUND: TypeAdapter[PlaybackReport] = TypeAdapter(PlayerInbound)
PLAYER_OUTBOUND: TypeAdapter[CreativeReady | CreativeUpgraded | ErrorMessage] = TypeAdapter(
    PlayerOutbound
)
CONSOLE_OUTBOUND: TypeAdapter[
    ConsoleMoment
    | ConsoleAuction
    | ConsoleLatency
    | ConsoleDegradation
    | CreativeReady
    | CreativeUpgraded
    | ErrorMessage
    | ConsoleBook
    | ConsoleReceipt
] = TypeAdapter(ConsoleOutbound)

AnyMessage = (
    AudienceSnapshot
    | AudienceKeyframe
    | AudienceUtterance
    | CreativeReady
    | CreativeUpgraded
    | PlaybackReport
    | ConsoleMoment
    | ConsoleAuction
    | ConsoleLatency
    | ConsoleDegradation
    | ErrorMessage
    | AdvertiserBidMultiplier
    | StockSet
    | ConsoleBook
    | ConsoleReceipt
)

# Order is the generated TS order; append new messages at the end.
ALL_MESSAGES: tuple[type[_Message], ...] = (
    AudienceSnapshot,
    AudienceKeyframe,
    AudienceUtterance,
    CreativeReady,
    CreativeUpgraded,
    PlaybackReport,
    ConsoleMoment,
    ConsoleAuction,
    ConsoleLatency,
    ConsoleDegradation,
    ErrorMessage,
    AdvertiserBidMultiplier,
    StockSet,
    ConsoleBook,
    ConsoleReceipt,
)

# Socket unions exported to TS, by name.
SOCKET_UNIONS: dict[str, tuple[type[_Message], ...]] = {
    "AgentInbound": (AudienceSnapshot, AudienceKeyframe, AudienceUtterance),
    "PlayerInbound": (PlaybackReport,),
    "PlayerOutbound": (CreativeReady, CreativeUpgraded, ErrorMessage),
    "ConsoleOutbound": (
        ConsoleMoment,
        ConsoleAuction,
        ConsoleLatency,
        ConsoleDegradation,
        CreativeReady,
        CreativeUpgraded,
        ErrorMessage,
        ConsoleBook,
        ConsoleReceipt,
    ),
}
