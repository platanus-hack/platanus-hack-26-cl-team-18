"""Brief + draft → wire `CreativeSpec`. Logos are the kits' real assets (invariant 4)."""

from __future__ import annotations

from collections.abc import Mapping

from app.contract.messages import (
    CopySourceKind,
    CreativeOffer,
    CreativeSlot,
    CreativeSpec,
    CreativeStage,
    CreativeStageName,
    MoneyDTO,
)
from app.modules.campaigns.domain.book import BrandId, BrandKit
from app.modules.creative.domain.brief import CreativeBrief
from app.modules.creative.domain.draft import CopyDraft

DURATION_MS = 12_000  # teaser 3 s + offer 5 s + close 4 s; the player holds on attention


def render_spec(
    brief: CreativeBrief, draft: CopyDraft, kits: Mapping[BrandId, BrandKit]
) -> CreativeSpec:
    slots = [
        CreativeSlot(
            brand_id=str(b.brand_id),
            brand_name=b.name,
            logo_url=kits[b.brand_id].logo_url,
            # The lead slot carries the offer line (price included); partners keep their kit copy.
            copy_text=draft.offer if index == 0 else b.stock_copy,
            accent_color=kits[b.brand_id].accent_color,
        )
        for index, b in enumerate(brief.brands)
    ]
    offer = brief.offer
    return CreativeSpec(
        layout_id=brief.layout,
        slots=slots,
        duration_ms=DURATION_MS,
        stages=[
            CreativeStage(name=CreativeStageName(stage.value), copy_text=text)
            for stage, text in draft.stages()
        ],
        offer=(
            CreativeOffer(
                label=offer.label,
                text=offer.text,
                price=MoneyDTO(
                    amount=str(offer.price.amount), currency=offer.price.currency.value.value
                ),
                party_size=offer.min_party,  # the group the approved price is for (<= 12)
            )
            if offer is not None
            else None
        ),
        angle=brief.angle.angle.value,
        copy_source=CopySourceKind(draft.source.value),
    )


def render_copy_upgrade(
    previous: CreativeSpec,
    brief: CreativeBrief,
    draft: CopyDraft,
    kits: Mapping[BrandId, BrandKit],
) -> CreativeSpec:
    """Replace copy only; the current basket owns its offer, redemption and media.

    A copy upgrade must not remove a pre-approved loop, reissue the basket's
    redemption code or reset any already-upgraded hero imagery. A changed
    spoken line is resolved separately through the guarded voice cache.
    """
    rendered = render_spec(brief, draft, kits)
    by_brand = {slot.brand_id: slot.copy_text for slot in rendered.slots}
    if {slot.brand_id for slot in previous.slots} != set(by_brand):
        raise ValueError("Copy upgrades must keep the basket's brands")
    return previous.model_copy(
        update={
            "slots": [
                slot.model_copy(update={"copy_text": by_brand[slot.brand_id]})
                for slot in previous.slots
            ],
            "stages": rendered.stages,
            "copy_source": rendered.copy_source,
            "voiceover_url": None,
        }
    )
