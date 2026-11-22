"""Every brand and basket finds its imagery, or deliberately airs the wordmark template.

`load_campaign_media` skips a manifest entry whose primary photo is missing, and the
studio (CampaignGenerator) then drops every brand that resolved nothing: a campaign
the demo relies on vanishes from /studio with no error. These checks name the gap.
"""

from __future__ import annotations

import json

import pytest

from app.modules.creative.application.campaigns import category_key, media_for
from app.modules.creative.infrastructure.campaign_assets import CAMPAIGN_ASSETS
from tests.unit.catalog_sweep.markets import CATALOGS, seeded

MANIFEST = json.loads((CAMPAIGN_ASSETS / "manifest.json").read_text())["campaigns"]

# Photos the manifest names that were still being generated when this sweep was written
# (Chile category scenes and route alternates). Until they are committed, the entries
# whose primary is listed here do not load. Remove a name once its file lands; a missing
# file NOT listed here fails the test.
PENDING_PHOTOS: frozenset[str] = frozenset()  # every manifest photo is committed

# Fictional brands without bundled photography: they air the wordmark template on
# purpose (the pinned seed has no category scenes). A new brand must be added here or
# get photos, so "no media" is always a decision.
WORDMARK_ONLY = {
    "fictional": frozenset(
        {"miga", "brasa-burger", "toro-burger", "cola-sur", "pacifica", "crunchi", "agua-cumbre"}
    ),
    "chile": frozenset(),
}


def _photos(entry: dict[str, object]) -> list[str]:
    alternates = entry.get("alternates", [])
    assert isinstance(alternates, list)
    return [str(entry["photo"]), *map(str, alternates)]


def test_every_photo_the_manifest_names_exists_or_is_known_pending() -> None:
    missing = {
        name
        for entry in MANIFEST.values()
        for name in _photos(entry)
        if not (CAMPAIGN_ASSETS / "photos" / f"{name}.webp").is_file()
    }
    assert missing <= PENDING_PHOTOS, sorted(missing - PENDING_PHOTOS)


@pytest.mark.parametrize("catalog", CATALOGS)
def test_every_brand_declares_imagery_or_is_wordmark_only(catalog: str) -> None:
    market = seeded(catalog)
    scenes = market.catalog.category_scenes
    undeclared = sorted(
        brand.slug
        for brand in market.catalog.brands
        if brand.slug not in MANIFEST
        and not (scenes and category_key(brand.category) in MANIFEST)
        and brand.slug not in WORDMARK_ONLY[catalog]
    )
    assert not undeclared, undeclared
    # And a wordmark-only brand really has nothing (else drop it from the list).
    assert not {slug for slug in WORDMARK_ONLY[catalog] if slug in MANIFEST}


@pytest.mark.parametrize("catalog", CATALOGS)
def test_every_declared_brand_and_basket_resolves_media_unless_its_photo_is_pending(
    catalog: str,
) -> None:
    market = seeded(catalog)
    media = market.market.campaign_media
    categories = {brand.slug: brand.category for brand in market.catalog.brands}

    def pending(slugs: list[str]) -> bool:
        keys = [
            "+".join(sorted(slugs)),
            *(k for s in slugs for k in (s, category_key(categories[s]))),
        ]
        declared = [MANIFEST[k] for k in keys if k in MANIFEST]
        return bool(declared) and all(str(e["photo"]) in PENDING_PHOTOS for e in declared)

    lost = []
    for basket in market.catalog.baskets:
        slugs = sorted(basket.slugs)
        if all(s in WORDMARK_ONLY[catalog] for s in slugs):
            continue
        if media_for(media, [(s, categories[s]) for s in slugs]) is None and not pending(slugs):
            lost.append("+".join(slugs))
    assert not lost, lost
