"""Build a renderable wire spec from approved brand-kit snapshots."""

from app.contract.messages import CreativeSlot, CreativeSpec
from app.modules.campaigns.domain.book import BrandKit


def compose_template(
    layout_id: str, kits: tuple[BrandKit, ...], *, approved_offer: str | None = None
) -> CreativeSpec:
    return CreativeSpec(
        layout_id=layout_id,
        duration_ms=8000,
        slots=[
            CreativeSlot(
                brand_id=str(kit.brand_id),
                brand_name=kit.name,
                logo_url=kit.logo_url,
                copy_text=approved_offer if index == 0 and approved_offer else kit.stock_copy,
                accent_color=kit.accent_color,
            )
            for index, kit in enumerate(kits)
        ],
    )
