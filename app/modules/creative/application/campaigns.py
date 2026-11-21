"""Instant campaign drafts from the live catalog: approved kits, offers and cached media."""

from collections import OrderedDict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from threading import Lock
from time import perf_counter

from app.contract.messages import (
    CampaignBrand,
    CampaignCatalog,
    CampaignGenerate,
    CampaignGenerated,
    CreativeSpec,
)
from app.modules.audience.domain.value_objects.segment import (
    SegmentEstimate,
    SegmentId,
    SegmentSource,
)
from app.modules.campaigns.domain.book import Bidder, BrandId, BrandKit
from app.modules.common.application.clock import BaseClock
from app.modules.common.application.idempotency import compute_request_hash
from app.modules.common.domain.value_objects.percentage import Percentage
from app.modules.creative.application.briefing import CreativeCatalog, MomentContext, assemble_brief
from app.modules.creative.application.rendering import render_spec
from app.modules.creative.application.studio import CreativeStudio
from app.modules.creative.domain.angle import Angle
from app.modules.creative.domain.brief import Daypart
from app.modules.creative.domain.offer import OfferBook

MOMENT_SEGMENTS = {
    "post_workout": ("408", Angle.POST_WORKOUT),
    "coffee_break": ("977", Angle.COFFEE_BREAK),
    "hunger": ("1404", Angle.HUNGER),
    "energy": ("997", Angle.ENERGY),  # Purchase Intent | Beverages | Energy Drinks
}


@dataclass(frozen=True, slots=True)
class CampaignMedia:
    photo_url: str
    video_url: str | None = None


def apply_campaign_media(
    spec: CreativeSpec, media: CampaignMedia | None, *, video: bool = True
) -> CreativeSpec:
    if media is None:
        return spec
    return spec.model_copy(
        update={
            "slots": [
                slot.model_copy(update={"hero_image_url": media.photo_url}) for slot in spec.slots
            ],
            "video_url": media.video_url if video else None,
        }
    )


class CampaignGenerator:
    """No provider calls or file IO: prepare cold assets once, then compose and memoize."""

    def __init__(
        self,
        *,
        kits: Sequence[BrandKit],
        bidders: Sequence[Bidder],
        offers: OfferBook,
        slugs: Mapping[BrandId, str],
        media: Mapping[str, CampaignMedia],
        clock: BaseClock,
    ) -> None:
        self._kits = {kit.brand_id: kit for kit in kits}
        self._bidders = {bidder.brand_id: bidder for bidder in bidders}
        self._slugs = dict(slugs)
        self._ids = {slug: brand for brand, slug in slugs.items()}
        self._media = dict(media)
        self._catalog = CreativeCatalog(
            kits=self._kits,
            offers=offers,
            segment_angles={
                SegmentId.create(segment): angle for segment, angle in MOMENT_SEGMENTS.values()
            },
        )
        self._studio = CreativeStudio(clock=clock)
        self._memo: OrderedDict[str, CampaignGenerated] = OrderedDict()
        self._lock = Lock()

    def _ready(self, brand: BrandId) -> bool:
        """Studio-ready: the brand has its own campaign photography bundled.

        Brands without it still bid live and air with their wordmark template; the
        studio never borrows another product's scenery for them.
        """
        return self._slugs[brand] in self._media

    def catalog(self) -> CampaignCatalog:
        return CampaignCatalog(
            brands=[
                CampaignBrand(
                    slug=self._slugs[brand],
                    name=kit.name,
                    category=self._bidders[brand].category,
                    accent_color=kit.accent_color,
                    logo_url=kit.logo_url,
                    photo_url=self._media[self._slugs[brand]].photo_url
                    if self._slugs[brand] in self._media
                    else None,
                    partners=sorted(self._slugs[p] for p in self._bidders[brand].partners),
                    age_restricted=self._bidders[brand].age_restricted,
                )
                for brand, kit in self._kits.items()
                if self._ready(brand)
            ],
            approved_baskets=[
                sorted(self._slugs[b] for b in basket)
                for basket in sorted(
                    self._catalog.offers.pairings(), key=lambda bs: sorted(str(b) for b in bs)
                )
                if all(self._ready(b) for b in basket)
            ],
        )

    def generate(self, request: CampaignGenerate) -> CampaignGenerated:
        started = perf_counter()
        slugs = tuple(request.brands)  # slot order: the lead brand first
        if len(set(slugs)) != len(slugs) or any(
            slug not in self._ids or not self._ready(self._ids[slug]) for slug in slugs
        ):
            raise ValueError("Selecciona una o dos marcas diferentes del catálogo")
        ids = tuple(self._ids[slug] for slug in slugs)
        if request.moment == "complete_basket" and len(ids) != 2:
            raise ValueError("Completar la compra requiere dos marcas complementarias")
        bidders = tuple(self._bidders[brand] for brand in ids)
        if any(
            not bidder.active or (request.minor_likely and bidder.age_restricted)
            for bidder in bidders
        ):
            raise ValueError("Esta campaña no es apta para el momento seleccionado")
        if len(ids) == 2:
            left, right = bidders
            if (
                left.category == right.category
                or right.brand_id not in left.partners
                or left.brand_id not in right.partners
                or frozenset(ids) not in self._catalog.offers.pairings()
            ):
                raise ValueError(
                    "La combinación requiere marcas complementarias y una oferta aprobada"
                )
        key = compute_request_hash(request.model_dump(mode="json"))[:24]
        with self._lock:
            if key in self._memo:
                self._memo.move_to_end(key)
                return self._memo[key].model_copy(
                    update={"cache_hit": True, "compose_ms": (perf_counter() - started) * 1000}
                )
            segment = MOMENT_SEGMENTS.get(request.moment)
            segments = (
                ()
                if segment is None
                else (
                    SegmentEstimate.create(
                        segment_id=SegmentId.create(segment[0]),
                        viewer_count=request.party_size,
                        confidence=Percentage.create(100),
                        sources=frozenset({SegmentSource.VISION}),
                    ),
                )
            )
            context = MomentContext(
                queue_length=max(2, request.party_size) if request.moment == "queue" else 0,
                held_brand_ids=frozenset(ids[:1])
                if request.moment == "complete_basket"
                else frozenset(),
                daypart=Daypart(request.daypart) if request.daypart else None,
            )
            brief = assemble_brief(
                basket=tuple((brand, self._bidders[brand].category) for brand in ids),
                layout="duo" if len(ids) == 2 else "solo",
                segments=segments,
                looking=request.party_size,
                people=request.party_size,
                minor_likely=request.minor_likely,
                catalog=self._catalog,
                context=context,
            )
            composed = self._studio.compose(brief)
            if not composed.score.verdict.passed:
                raise ValueError("La campaña no superó la revisión de copy")
            spec = apply_campaign_media(
                render_spec(brief, composed.draft, self._kits),
                self._media.get("+".join(sorted(slugs))),
                video=request.media == "video",
            )
            result = CampaignGenerated(
                campaign_key=key,
                title=brief.offer.label if brief.offer else " · ".join(brief.names),
                brands=list(slugs),
                party_size=request.party_size,
                moment=request.moment,
                spec=spec,
                quality_score=str(composed.score.total),
                checks=[
                    "real_brand_assets",
                    "approved_pairing",
                    "guarded_copy",
                    "fixed_offer_price",
                    "minor_safety",
                ],
                compose_ms=(perf_counter() - started) * 1000,
                cache_hit=False,
            )
            self._memo[key] = result
            while len(self._memo) > 128:
                self._memo.popitem(last=False)
            return result
