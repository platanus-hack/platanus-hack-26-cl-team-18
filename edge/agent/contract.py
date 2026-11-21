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

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    FiniteFloat,
    TypeAdapter,
    model_validator,
)

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
    dwell_s: Annotated[FiniteFloat, Field(ge=0.0)]
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


class PerceptionTiming(_Model):
    """Sensor timings only: no images, provider payloads or per-person observations."""

    detector: Annotated[str, Field(min_length=1, max_length=64)]
    inference_ms: Annotated[FiniteFloat, Field(ge=0)]
    round_trip_ms: Annotated[FiniteFloat, Field(ge=0)] | None = None
    primary_attempt_ms: Annotated[FiniteFloat, Field(ge=0)] | None = None


class LatencyTrace(_Model):
    """Every decision carries one; add a field when you add a step to the loop."""

    # Device-clock boundaries: candidate includes track confirmation; measured_at
    # remains the latest frame used for the published facts. Synchronize device,
    # backend and player clocks before comparing timestamps across machines.
    candidate_at: AwareDatetime | None = None
    detected_at: AwareDatetime | None = None
    published_at: AwareDatetime | None = None
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
    perception: PerceptionTiming | None = None


class CreativeSlot(_Model):
    brand_id: EntityId
    brand_name: Annotated[str, Field(min_length=1, max_length=80)]
    logo_url: str  # the brand kit's real asset; never AI-generated
    copy_text: Annotated[str, Field(max_length=280)]
    accent_color: Annotated[str, Field(pattern=r"^#[0-9a-fA-F]{6}$")] | None = None
    hero_image_url: str | None = None


class CreativeStageName(StrEnum):
    TEASER = "teaser"  # 0-3 s: big visuals, logos
    OFFER = "offer"  # 3-8 s: the basket offer sized to the group
    CLOSE = "close"  # 8 s+: QR + "valid 10 min"


class CreativeStage(_Model):
    name: CreativeStageName
    copy_text: Annotated[str, Field(min_length=1, max_length=280)]


class CreativeOffer(_Model):
    """The approved basket offer the creative is sized to (price never AI-written)."""

    label: Annotated[str, Field(min_length=1, max_length=80)]
    text: Annotated[str, Field(min_length=1, max_length=160)]
    price: MoneyDTO
    party_size: Annotated[int, Field(ge=1, le=12)]


class CopySourceKind(StrEnum):
    STOCK = "stock"
    AI = "ai"
    CACHE = "cache"


class CreativeSpec(_Model):
    layout_id: EntityId
    slots: Annotated[list[CreativeSlot], Field(min_length=1, max_length=3)]
    duration_ms: Annotated[int, Field(ge=1000, le=60_000)]
    video_url: str | None = None
    voiceover_url: str | None = None
    qr_url: str | None = None
    # Dwell-stage copy (architecture §6.1); empty = render the slots' copy only.
    stages: Annotated[list[CreativeStage], Field(max_length=3)] = Field(default_factory=list)
    offer: CreativeOffer | None = None
    # Why this ad: the angle the copy answers (hunger, queue, ...) and who wrote it.
    angle: Annotated[str, Field(max_length=32)] | None = None
    copy_source: CopySourceKind | None = None
    # The basket's redemption code (QR → /r/{code}); `qr_url` is its rendered QR image.
    redemption_code: Annotated[str, Field(pattern=r"^[A-Z0-9]{4,12}$")] | None = None
    redeem_url: Annotated[str, Field(max_length=500)] | None = None


# ---------------------------------------------------------------- agent -> backend


class AudienceSnapshot(_Message):
    type: Literal["audience.snapshot"] = "audience.snapshot"
    screen_id: ScreenId
    seq: Seq
    measured_at: AwareDatetime
    # Device-clock boundaries: candidate includes track confirmation; measured_at
    # remains the latest frame used for the published facts. Synchronize device,
    # backend and player clocks before comparing timestamps across machines.
    candidate_at: AwareDatetime | None = None
    detected_at: AwareDatetime | None = None
    published_at: AwareDatetime | None = None
    people: Annotated[int, Field(ge=0)]
    looking: Annotated[int, Field(ge=0)]
    tracklets: list[Tracklet] = Field(default_factory=list, max_length=64)
    objects: list[DetectedObject] = Field(default_factory=list, max_length=64)
    sound_scene: list[SoundScene] = Field(default_factory=list, max_length=16)
    minor_likely: MinorLikely
    perception: PerceptionTiming | None = None

    @model_validator(mode="after")
    def _looking_within_people(self) -> AudienceSnapshot:
        if self.looking > self.people:
            raise ValueError("looking cannot exceed people")
        return self


class RecognitionAttribute(_Model):
    name: Annotated[str, Field(min_length=1, max_length=64)]
    value: Annotated[str, Field(max_length=256)]
    source: Literal["vlm", "visual", "audio"]
    observed_at_s: Annotated[float, Field(ge=0)]
    evidence: Annotated[str, Field(max_length=500)]
    uncertainty: Literal["low", "medium", "high", "unknown"] = "unknown"
    model: Annotated[str, Field(max_length=128)] = ""
    associated_item: Annotated[str, Field(max_length=128)] = ""
    crop_ref: Annotated[str, Field(max_length=128)] = ""


class RecognitionPerson(_Model):
    track_id: Annotated[str, Field(min_length=1, max_length=32)]
    first_seen_s: Annotated[float, Field(ge=0)]
    last_seen_s: Annotated[float, Field(ge=0)]
    box: list[float] = Field(min_length=4, max_length=4)
    confidence: Score
    direction: Literal["left", "right", "stationary", "unknown"]
    currently_visible: bool
    attributes: list[RecognitionAttribute] = Field(default_factory=list, max_length=64)


class RecognitionObject(_Model):
    label: Annotated[str, Field(min_length=1, max_length=64)]
    count: Annotated[int, Field(ge=1)]
    last_seen_s: Annotated[float, Field(ge=0)]
    currently_visible: bool


class RecognitionDetection(_Model):
    label: Annotated[str, Field(min_length=1, max_length=64)]
    confidence: Score
    box: list[float] = Field(min_length=4, max_length=4)
    track_id: Annotated[str, Field(min_length=1, max_length=32)] | None = None
    observed_at_s: Annotated[float, Field(ge=0)]


class RecognitionAudioEvent(_Model):
    label: Annotated[str, Field(min_length=1, max_length=64)]
    observed_at_s: Annotated[float, Field(ge=0)]
    confidence: Score
    source: Annotated[str, Field(min_length=1, max_length=64)]


class RecognitionLatency(_Model):
    decode_ms: Annotated[float, Field(ge=0)]
    detection_tracking_ms: Annotated[float, Field(ge=0)]
    publication_delay_ms: Annotated[float, Field(ge=0)]
    first_observation_ms: Annotated[float, Field(ge=0)] | None = None


class RecognitionSnapshot(_Message):
    """Anonymous, ephemeral recognition facts; not a classified audience moment."""

    type: Literal["recognition.snapshot"] = "recognition.snapshot"
    screen_id: ScreenId
    session_id: EntityId
    session_started_at: AwareDatetime
    seq: Seq
    revision: Seq = 0  # increases when warm observations enrich this session
    measured_at: AwareDatetime
    video_time_s: Annotated[float, Field(ge=0)]
    finished: bool = False
    people: list[RecognitionPerson] = Field(default_factory=list, max_length=128)
    history: list[RecognitionPerson] = Field(default_factory=list, max_length=256)
    detections: list[RecognitionDetection] = Field(default_factory=list, max_length=256)
    objects: list[RecognitionObject] = Field(default_factory=list, max_length=128)
    audio_events: list[RecognitionAudioEvent] = Field(default_factory=list, max_length=32)
    latency: RecognitionLatency


class AudienceKeyframe(_Message):
    """Header for a face-blurred JPEG; the bytes follow as the next binary WS frame."""

    type: Literal["audience.keyframe"] = "audience.keyframe"
    screen_id: ScreenId
    seq: Seq
    measured_at: AwareDatetime
    content_type: Literal["image/jpeg"] = "image/jpeg"
    byte_length: Annotated[int, Field(gt=0, le=2_000_000)]
    session_id: EntityId | None = None
    track_id: Annotated[str, Field(min_length=1, max_length=32)] | None = None
    video_time_s: Annotated[float, Field(ge=0)] | None = None


class AudienceUtterance(_Message):
    type: Literal["audience.utterance"] = "audience.utterance"
    screen_id: ScreenId
    seq: Seq
    measured_at: AwareDatetime
    text: Annotated[str, Field(max_length=1000)]  # ephemeral: never persisted or logged
    language: Annotated[str, Field(min_length=2, max_length=16)]
    keyterms_hit: list[str] = Field(default_factory=list, max_length=32)
    is_final: bool = True  # partial corrections may retract earlier keyword hits
    utterance_id: Annotated[str, Field(min_length=1, max_length=64)] | None = None


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


class PlayerDisplayed(_Message):
    """The player made a creative visible (crossfade done): closes glass-to-glass latency."""

    type: Literal["player.displayed"] = "player.displayed"
    screen_id: ScreenId
    creative_id: EntityId
    seq: Seq
    displayed_at: AwareDatetime


class PlayerVoice(_Message):
    """Actual Web Audio playback, used only for ephemeral echo rejection."""

    type: Literal["player.voice"] = "player.voice"
    screen_id: ScreenId
    creative_id: EntityId
    seq: Seq
    playing: bool
    duration_ms: Annotated[int, Field(ge=0, le=120000)]


# ---------------------------------------------------------------- backend -> player


class ScreenAttention(_Message):
    """Who is watching now (aggregate only): the player advances dwell stages on it.

    `dwell_s` is the longest current look among looking viewers; 0 when nobody looks,
    so the player holds its stage instead of advancing on a timer (architecture §6.1).
    """

    type: Literal["screen.attention"] = "screen.attention"
    screen_id: ScreenId
    creative_id: EntityId | None = None
    looking: Annotated[int, Field(ge=0)]
    dwell_s: Annotated[FiniteFloat, Field(ge=0.0)]
    # The nearest looking viewer's band: the player sizes the layout for it
    # (far: headline and price only; near: the QR up front). None = unknown.
    distance_band: DistanceBand | None = None
    measured_at: AwareDatetime


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


class SegmentMatchKind(StrEnum):
    EXACT = "exact"
    BROADER = "broader"  # the bid's segment is an ancestor of the moment's
    NARROWER = "narrower"  # the bid's segment is a descendant of the moment's
    RELATED = "related"  # a curated same-need pair from the catalog


class BidMatch(_Model):
    """How a bid reached one segment of the moment, and what share of its rate it kept."""

    segment_id: SegmentId  # the moment's segment
    bid_segment_id: SegmentId  # the segment the brand bid on
    kind: SegmentMatchKind
    weight: Score  # affinity: 1.0 exact, less for an approximate match
    segment_label: Annotated[str, Field(max_length=80)] = ""
    bid_segment_label: Annotated[str, Field(max_length=80)] = ""


class BidLine(_Model):
    campaign_id: EntityId
    brand_name: Annotated[str, Field(max_length=80)]
    category: Annotated[str, Field(max_length=64)]
    bid_per_as: MoneyDTO  # per attention-second
    value: Annotated[float, Field(ge=0.0)]  # relevance-weighted score, not money
    value_per_s: MoneyDTO | None = None  # the bid's money value per screen-second
    multiplier: Annotated[str, Field(pattern=r"^\d+(\.\d+)?$", max_length=16)] | None = None
    matches: list[BidMatch] = Field(default_factory=list, max_length=32)


class Exclusion(_Model):
    campaign_id: EntityId
    reason: ExclusionReason
    detail: Annotated[str, Field(max_length=200)] = ""


class Winner(_Model):
    campaign_id: EntityId
    brand_name: Annotated[str, Field(max_length=80)]
    price_per_as: MoneyDTO
    payment_per_s: MoneyDTO | None = None


class BundleAlternative(_Model):
    """A basket the auction weighed (best first): the console's "Canastas evaluadas"."""

    brands: Annotated[list[Annotated[str, Field(max_length=80)]], Field(min_length=1, max_length=2)]
    value: MoneyDTO  # per screen-second


class ConsoleAuction(_Message):
    type: Literal["console.auction"] = "console.auction"
    screen_id: ScreenId
    auction_id: EntityId
    moment_id: EntityId
    bids: list[BidLine]
    exclusions: list[Exclusion]
    winners: list[Winner]  # empty = no fill (house ad)
    offer: CreativeOffer | None = None  # approved offer on the corresponding creative
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
    alternatives: list[BundleAlternative] = Field(default_factory=list, max_length=6)


class ConsoleNext(_Message):
    """The creative waiting out the on-air one's minimum time (a held swap): a preview.

    Station consoles only (?live=1). The preview has no QR, code or voice yet; those
    are made when it airs. `auction_id` None = the house ad is next.
    """

    type: Literal["console.next"] = "console.next"
    screen_id: ScreenId
    auction_id: EntityId | None = None
    spec: CreativeSpec
    not_before: AwareDatetime


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


class AdvertiserAutoBid(_Message):
    """Turn rule-based auto-bidding on or off for one campaign (a manual slider move turns it off)."""

    type: Literal["advertiser.autobid"] = "advertiser.autobid"
    campaign_id: EntityId
    enabled: bool
    target_cost_per_redemption: MoneyDTO | None = None


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
    autobid: bool = False
    autobid_rule: Annotated[str, Field(max_length=32)] | None = None
    autobid_reason: Annotated[str, Field(max_length=200)] | None = None


class ConsoleBook(_Message):
    """The live campaign book for one screen: bids, stock and budgets (also GET /api/book)."""

    type: Literal["console.book"] = "console.book"
    screen_id: ScreenId
    lines: list[BookLine]


class ChargeReason(StrEnum):
    ATTENTION = "attention"
    NO_ATTENTION = "no_attention"
    BUDGET_CAP = "budget_cap"


class ChargeLine(_Model):
    campaign_id: EntityId
    brand_name: Annotated[str, Field(max_length=80)]
    attention_seconds: Annotated[str, Field(pattern=r"^\d+(\.\d+)?$", max_length=16)]
    price_per_as: MoneyDTO
    amount: MoneyDTO
    reason: ChargeReason


class FilteredAttention(_Model):
    reason: Annotated[str, Field(max_length=32)]
    seconds: Annotated[str, Field(pattern=r"^\d+(\.\d+)?$", max_length=16)]


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
    # Invalid-attention filtering: seconds not billed, per reason (glance, far, static, ...).
    filtered: list[FilteredAttention] = Field(default_factory=list, max_length=8)


class ConsoleRedemption(_Message):
    """A basket's QR was redeemed: credited to every brand in it (architecture §6.2)."""

    type: Literal["console.redemption"] = "console.redemption"
    screen_id: ScreenId
    auction_id: EntityId
    code: Annotated[str, Field(pattern=r"^[A-Z0-9]{4,12}$")]
    brands: list[Annotated[str, Field(max_length=80)]]
    offer_text: Annotated[str, Field(max_length=160)] | None = None
    redeemed_at: AwareDatetime


# ---------------------------------------------------------------- any direction


class ErrorMessage(_Message):
    """Sent back on the same socket when an inbound message is rejected."""

    type: Literal["system.error"] = "system.error"
    code: Annotated[str, Field(max_length=64)]
    message: Annotated[str, Field(max_length=500)]
    rejected_type: str | None = None


# ---------------------------------------------------------------- live switch (station control)


class AgentRole(StrEnum):
    VISION = "vision"
    SPEECH = "speech"


class AgentHello(_Message):
    """First frame on every agent connection: the screen and sensor it serves.

    The backend answers with that screen's `live.status` and pushes every change,
    so an agent that reconnects learns the current switch position at once.
    """

    type: Literal["agent.hello"] = "agent.hello"
    screen_id: ScreenId
    role: AgentRole
    seq: Seq = 0


class LiveSet(_Message):
    """The console's "En vivo" switch (HTTP POST /api/live).

    Absolute values, so a retry is harmless and the last click wins. A field left
    `None` keeps its value: the master `live` and the `mic` sub-switch are
    independent axes.
    """

    type: Literal["live.set"] = "live.set"
    screen_id: ScreenId
    live: bool | None = None
    mic: bool | None = None


class LiveStatus(_Message):
    """Where a screen's live switch stands, pushed to the console, player and agents.

    `sensing`, `listening` and `ai_generation` are derived, never set directly:
    sensing = live; listening = live and mic and speech_opt_in (ADR 0005);
    ai_generation = live (warm AI copy, voice-overs, VLM tags and speech topics).
    """

    type: Literal["live.status"] = "live.status"
    screen_id: ScreenId
    revision: Seq
    live: bool
    mic: bool
    speech_opt_in: bool
    sensing: bool
    listening: bool
    ai_generation: bool
    agents: list[AgentRole] = Field(default_factory=list, max_length=4)
    changed_at: AwareDatetime


# ---------------------------------------------------------------- dev / QA (`/ws/dev`)
#
# Debug views of the loop for `/dev` (docs/dev-console-plan.md). Never sent in prod,
# never stored. No pixels, audio, transcripts or identity: boxes are normalized
# geometry of anonymous, session-local tracks. `dev.keyframe` is the one exception:
# the same face-blurred JPEG the VLM gets, opt-in (DEV_KEYFRAME_PREVIEW) and local-only.

DevKind = Literal["tag", "topics", "copy", "voice", "video", "other"]


class DevBox(_Model):
    """Normalized [0, 1] corners in the frame the detector saw."""

    x1: Score
    y1: Score
    x2: Score
    y2: Score


class DevPerson(_Model):
    track_id: Annotated[str, Field(min_length=1, max_length=32)]
    box: DevBox
    confidence: Score
    looking: bool
    distance_band: DistanceBand
    dwell_s: Annotated[FiniteFloat, Field(ge=0.0)]


class DevObject(_Model):
    label: Annotated[str, Field(min_length=1, max_length=64)]
    confidence: Score
    box: DevBox
    near_person: bool  # within a tracked person's reach (counts as held/carried)


class DevMarker(_Model):
    marker_id: Annotated[int, Field(ge=0)]
    brand_ref: Annotated[str, Field(max_length=64)] | None = None
    box: DevBox


class DevPerception(_Message):
    """What YOLO saw on one frame, after tracking (agent -> backend -> dev, <= 5 Hz)."""

    type: Literal["dev.perception"] = "dev.perception"
    screen_id: ScreenId
    seq: Seq
    measured_at: AwareDatetime
    frame_w: Annotated[int, Field(ge=1)]
    frame_h: Annotated[int, Field(ge=1)]
    detector: Annotated[str, Field(min_length=1, max_length=64)]
    inference_ms: Annotated[FiniteFloat, Field(ge=0)]
    round_trip_ms: Annotated[FiniteFloat, Field(ge=0)] | None = None
    raw_people: Annotated[int, Field(ge=0)]  # detections before the near/track filter
    people: list[DevPerson] = Field(default_factory=list, max_length=64)
    objects: list[DevObject] = Field(default_factory=list, max_length=64)
    markers: list[DevMarker] = Field(default_factory=list, max_length=16)
    held: list[Annotated[str, Field(max_length=64)]] = Field(default_factory=list, max_length=16)
    frames: Annotated[int, Field(ge=0)] = 0
    stale_frames: Annotated[int, Field(ge=0)] = 0


class DevTaggedSegment(_Model):
    segment_id: Annotated[str, Field(min_length=1, max_length=32)]
    label: Annotated[str, Field(max_length=80)]
    confidence: Score
    viewer_count: Annotated[int, Field(ge=0)]
    kept: bool
    drop_reason: Annotated[str, Field(max_length=32)] | None = None


class DevTaggedProduct(_Model):
    category: Annotated[str, Field(min_length=1, max_length=64)]
    brand_ref: Annotated[str, Field(max_length=64)] | None = None
    confidence: Score
    kept: bool
    drop_reason: Annotated[str, Field(max_length=32)] | None = None


class DevVlm(_Message):
    """One keyframe-tagging call: what the VLM returned and what the ACL kept."""

    type: Literal["dev.vlm"] = "dev.vlm"
    screen_id: ScreenId
    submitted_at: AwareDatetime
    finished_at: AwareDatetime
    latency_ms: Annotated[FiniteFloat, Field(ge=0)]
    outcome: Literal["ok", "failed", "stale"]
    model_version: Annotated[str, Field(max_length=128)] = ""
    segments: list[DevTaggedSegment] = Field(default_factory=list, max_length=32)
    held_products: list[DevTaggedProduct] = Field(default_factory=list, max_length=16)
    minor_likely: bool = False
    minor_likely_confidence: Score = 0.0
    reasoning: Annotated[str, Field(max_length=500)] = ""  # objects and context only
    error_type: Annotated[str, Field(max_length=64)] | None = None
    expires_at: AwareDatetime | None = None


class DevSpeechTopic(_Model):
    segment_id: Annotated[str, Field(min_length=1, max_length=32)]
    label: Annotated[str, Field(max_length=80)]
    confidence: Score | None = None
    expires_at: AwareDatetime | None = None


class DevSpeech(_Message):
    """Speech understanding without the words: keyterms, topics, outcome (ADR 0005)."""

    type: Literal["dev.speech"] = "dev.speech"
    screen_id: ScreenId
    at: AwareDatetime
    stage: Literal["keyterms", "extraction", "echo"]
    is_final: bool = True
    wanted: list[Annotated[str, Field(max_length=32)]] = Field(default_factory=list, max_length=32)
    rejected: list[Annotated[str, Field(max_length=32)]] = Field(
        default_factory=list, max_length=32
    )
    topics: list[DevSpeechTopic] = Field(default_factory=list, max_length=16)
    outcome: Annotated[str, Field(max_length=32)] | None = None
    model_version: Annotated[str, Field(max_length=128)] | None = None
    latency_ms: Annotated[FiniteFloat, Field(ge=0)] | None = None


class DevProviderLoad(_Model):
    provider: Annotated[str, Field(min_length=1, max_length=32)]
    in_flight: Annotated[int, Field(ge=0)]


class DevJob(_Message):
    """Warm-path job lifecycle (WarmWorker)."""

    type: Literal["dev.job"] = "dev.job"
    screen_id: ScreenId | None = None
    provider: Annotated[str, Field(min_length=1, max_length=32)]
    key: Annotated[str, Field(min_length=1, max_length=200)]
    kind: DevKind
    state: Literal["submitted", "deduped", "running", "done", "failed", "rejected"]
    at: AwareDatetime
    elapsed_ms: Annotated[FiniteFloat, Field(ge=0)] | None = None
    error_type: Annotated[str, Field(max_length=64)] | None = None
    load: list[DevProviderLoad] = Field(default_factory=list, max_length=16)


class DevViolation(_Model):
    kind: Annotated[str, Field(max_length=32)]
    where: Annotated[str, Field(max_length=16)]
    detail: Annotated[str, Field(max_length=160)] = ""


class DevCopy(_Model):
    source: CopySourceKind
    teaser: Annotated[str, Field(max_length=280)]
    offer: Annotated[str, Field(max_length=280)]
    close: Annotated[str, Field(max_length=280)]
    voice_line: Annotated[str, Field(max_length=280)]
    score: Annotated[str, Field(max_length=16)] | None = None
    passed: bool = True
    violations: list[DevViolation] = Field(default_factory=list, max_length=16)
    model_version: Annotated[str, Field(max_length=128)] | None = None


class DevBrief(_Model):
    brands: list[Annotated[str, Field(max_length=80)]] = Field(max_length=3)
    layout: Annotated[str, Field(max_length=32)]
    party: Annotated[int, Field(ge=0)]
    people: Annotated[int, Field(ge=0)]
    angle: Annotated[str, Field(max_length=32)]
    angle_reason: Annotated[str, Field(max_length=32)]
    offer_label: Annotated[str, Field(max_length=80)] | None = None
    offer_price: MoneyDTO | None = None
    held_brand: Annotated[str, Field(max_length=80)] | None = None
    daypart: Annotated[str, Field(max_length=16)] | None = None
    minor_likely: bool = False
    signature: Annotated[str, Field(max_length=200)]


class DevCreative(_Message):
    """One step of building the creative: compose, AI copy attempt, upgrades."""

    type: Literal["dev.creative"] = "dev.creative"
    screen_id: ScreenId
    at: AwareDatetime
    step: Literal["composed", "house", "copy_attempt", "copy_upgraded", "voice_upgraded"]
    creative_id: EntityId | None = None
    auction_id: EntityId | None = None
    moment_id: EntityId | None = None
    brief: DevBrief | None = None
    copy_draft: DevCopy | None = None
    outcome: Annotated[str, Field(max_length=32)] | None = None  # studio outcome
    ai_score: Annotated[str, Field(max_length=16)] | None = None
    stock_score: Annotated[str, Field(max_length=16)] | None = None
    violations: list[DevViolation] = Field(default_factory=list, max_length=16)
    latency_ms: Annotated[FiniteFloat, Field(ge=0)] | None = None
    compose_ms: Annotated[FiniteFloat, Field(ge=0)] | None = None
    voiceover_url: Annotated[str, Field(max_length=500)] | None = None
    video_url: Annotated[str, Field(max_length=500)] | None = None
    redemption_code: Annotated[str, Field(max_length=12)] | None = None
    note: Annotated[str, Field(max_length=200)] | None = None


class DevPlayer(_Message):
    """What the physical screen reported back (displayed, played, voice)."""

    type: Literal["dev.player"] = "dev.player"
    screen_id: ScreenId
    creative_id: EntityId
    event: Literal["displayed", "playback", "voice"]
    at: AwareDatetime
    played_s: Annotated[FiniteFloat, Field(ge=0)] | None = None
    voice_playing: bool | None = None


class DevKeyframe(_Message):
    """Opt-in preview: the face-blurred keyframe the VLM gets. Never stored or logged."""

    type: Literal["dev.keyframe"] = "dev.keyframe"
    screen_id: ScreenId
    measured_at: AwareDatetime
    jpeg_base64: Annotated[str, Field(min_length=1, max_length=2_700_000)]


# ---------------------------------------------------------------- unions per socket

AgentInbound = Annotated[
    AudienceSnapshot
    | AudienceKeyframe
    | AudienceUtterance
    | RecognitionSnapshot
    | AgentHello
    | DevPerception,
    Field(discriminator="type"),
]
AgentOutbound = Annotated[LiveStatus | ErrorMessage, Field(discriminator="type")]
PlayerInbound = Annotated[
    PlaybackReport | PlayerDisplayed | PlayerVoice, Field(discriminator="type")
]
PlayerOutbound = Annotated[
    CreativeReady | CreativeUpgraded | ErrorMessage | ScreenAttention | LiveStatus,
    Field(discriminator="type"),
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
    | ConsoleReceipt
    | ConsoleRedemption
    | LiveStatus
    | ConsoleNext
    | DevPerception,
    Field(discriminator="type"),
]
DevMessage = DevPerception | DevVlm | DevSpeech | DevJob | DevCreative | DevPlayer | DevKeyframe
DevOutbound = Annotated[
    ConsoleMoment
    | ConsoleAuction
    | ConsoleLatency
    | ConsoleDegradation
    | CreativeReady
    | CreativeUpgraded
    | ErrorMessage
    | ConsoleBook
    | ConsoleReceipt
    | ConsoleRedemption
    | LiveStatus
    | ConsoleNext
    | DevPerception
    | DevVlm
    | DevSpeech
    | DevJob
    | DevCreative
    | DevPlayer
    | DevKeyframe,
    Field(discriminator="type"),
]

AGENT_INBOUND: TypeAdapter[
    AudienceSnapshot
    | AudienceKeyframe
    | AudienceUtterance
    | RecognitionSnapshot
    | AgentHello
    | DevPerception
] = TypeAdapter(AgentInbound)
AGENT_OUTBOUND: TypeAdapter[LiveStatus | ErrorMessage] = TypeAdapter(AgentOutbound)
PLAYER_INBOUND: TypeAdapter[PlaybackReport | PlayerDisplayed | PlayerVoice] = TypeAdapter(
    PlayerInbound
)
PLAYER_OUTBOUND: TypeAdapter[
    CreativeReady | CreativeUpgraded | ErrorMessage | ScreenAttention | LiveStatus
] = TypeAdapter(PlayerOutbound)
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
    | ConsoleRedemption
    | LiveStatus
    | ConsoleNext
    | DevPerception
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
    | RecognitionSnapshot
    | PlayerDisplayed
    | PlayerVoice
    | ScreenAttention
    | ConsoleRedemption
    | AgentHello
    | LiveSet
    | LiveStatus
    | AdvertiserAutoBid
    | DevPerception
    | DevVlm
    | DevSpeech
    | DevJob
    | DevCreative
    | DevPlayer
    | DevKeyframe
    | ConsoleNext
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
    RecognitionSnapshot,
    PlayerDisplayed,
    PlayerVoice,
    ScreenAttention,
    ConsoleRedemption,
    AgentHello,
    LiveSet,
    LiveStatus,
    AdvertiserAutoBid,
    DevPerception,
    DevVlm,
    DevSpeech,
    DevJob,
    DevCreative,
    DevPlayer,
    DevKeyframe,
)


class CampaignBrand(_Model):
    slug: str
    name: str
    category: str
    accent_color: str
    logo_url: str
    photo_url: str | None = None
    partners: list[str]
    age_restricted: bool


class CampaignCatalog(_Message):
    type: Literal["campaign.catalog"] = "campaign.catalog"
    brands: list[CampaignBrand]
    approved_baskets: list[list[str]]


class CampaignGenerate(_Message):
    type: Literal["campaign.generate"] = "campaign.generate"
    # Slot order: the first brand leads (its logo first, the hook written for it).
    brands: Annotated[list[str], Field(min_length=1, max_length=2)]
    party_size: Annotated[int, Field(ge=1, le=12)] = 2
    moment: Literal[
        "post_workout", "coffee_break", "hunger", "energy", "queue", "complete_basket"
    ] = "post_workout"
    # The hour sharpens the hook ("El primer café del día"); None = any time.
    daypart: Literal["morning", "midday", "afternoon", "evening", "night"] | None = None
    minor_likely: bool = False
    media: Literal["photo", "video"] = "video"


class CampaignGenerated(_Message):
    type: Literal["campaign.generated"] = "campaign.generated"
    campaign_key: str
    title: str
    brands: list[str]
    party_size: int
    moment: str
    status: Literal["draft"] = "draft"
    spec: CreativeSpec
    quality_score: str
    checks: list[str]
    compose_ms: Annotated[float, Field(ge=0)]
    cache_hit: bool


# HTTP messages share the generated schemas without joining any socket union.
ALL_MESSAGES += (CampaignCatalog, CampaignGenerate, CampaignGenerated)
ALL_MESSAGES += (ConsoleNext,)

# Socket unions exported to TS, by name.
SOCKET_UNIONS: dict[str, tuple[type[_Message], ...]] = {
    "AgentInbound": (
        AudienceSnapshot,
        AudienceKeyframe,
        AudienceUtterance,
        RecognitionSnapshot,
        AgentHello,
        DevPerception,
    ),
    "AgentOutbound": (LiveStatus, ErrorMessage),
    "PlayerInbound": (PlaybackReport, PlayerDisplayed, PlayerVoice),
    "PlayerOutbound": (CreativeReady, CreativeUpgraded, ErrorMessage, ScreenAttention, LiveStatus),
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
        ConsoleRedemption,
        LiveStatus,
        ConsoleNext,
        DevPerception,
    ),
    "DevOutbound": (
        ConsoleMoment,
        ConsoleAuction,
        ConsoleLatency,
        ConsoleDegradation,
        CreativeReady,
        CreativeUpgraded,
        ErrorMessage,
        ConsoleBook,
        ConsoleReceipt,
        ConsoleRedemption,
        LiveStatus,
        ConsoleNext,
        DevPerception,
        DevVlm,
        DevSpeech,
        DevJob,
        DevCreative,
        DevPlayer,
        DevKeyframe,
    ),
}
