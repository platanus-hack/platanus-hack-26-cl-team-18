"""Campaign generator evaluation against the actual live catalog and bundled media."""

import json
from decimal import Decimal
from itertools import product
from pathlib import Path
from statistics import mean
from typing import Any

from PIL import Image

from app.bootstrap.demo import _slug, seed_market
from app.contract.messages import CampaignGenerate
from app.modules.common.application.id_generator import IdGenerator
from app.modules.common.infrastructure.clock.system_clock import SystemClock
from app.modules.creative.application.campaigns import CampaignGenerator
from app.modules.creative.infrastructure.campaign_assets import CAMPAIGN_ASSETS

MOMENTS = ("post_workout", "coffee_break", "hunger", "queue", "complete_basket")
PARTIES = (1, 2, 3, 4, 6, 12)


def evaluate_campaigns(*, gate_latency: bool = True) -> dict[str, Any]:
    market = seed_market(ids=IdGenerator(), budget_clp=50_000)
    slugs = {kit.brand_id: _slug(kit.name) for kit in market.kits}
    generator = CampaignGenerator(
        kits=market.kits,
        bidders=market.bidders,
        offers=market.offers,
        slugs=slugs,
        media=market.campaign_media,
        clock=SystemClock(),
    )
    catalog = generator.catalog()
    combinations = [[brand.slug] for brand in catalog.brands] + catalog.approved_baskets
    by_slug = {brand.slug: brand for brand in catalog.brands}
    kits = {slugs[kit.brand_id]: kit for kit in market.kits}
    failures: list[str] = []
    times: list[float] = []
    qualities: list[float] = []
    blocked = cached = 0
    for brands, party, moment, minor, media in product(
        combinations, PARTIES, MOMENTS, (False, True), ("photo", "video")
    ):
        request = CampaignGenerate.model_validate(
            dict(brands=brands, party_size=party, moment=moment, minor_likely=minor, media=media)
        )
        name = f"{'+'.join(brands)} / {party} / {moment} / minor={minor} / {media}"
        forbidden = (minor and any(by_slug[brand].age_restricted for brand in brands)) or (
            moment == "complete_basket" and len(brands) == 1
        )
        try:
            generated = generator.generate(request)
        except ValueError:
            if not forbidden:
                failures.append(f"{name}: valid campaign refused")
            else:
                blocked += 1
            continue
        if forbidden:
            failures.append(f"{name}: unsafe or nonsensical campaign accepted")
        times.append(generated.compose_ms)
        qualities.append(float(generated.quality_score))
        repeated = generator.generate(request.model_copy(update={"brands": list(reversed(brands))}))
        cached += int(
            repeated.cache_hit
            and repeated.spec == generated.spec
            and repeated.campaign_key == generated.campaign_key
        )
        ids = frozenset(kits[brand].brand_id for brand in brands)
        expected_offer = market.offers.for_party(ids, party)
        if expected_offer is not None and (
            generated.spec.offer is None
            or generated.spec.offer.price.amount != str(expected_offer.price.amount)
        ):
            failures.append(f"{name}: approved offer price changed")
        if Decimal(generated.quality_score) < Decimal("0.6"):
            failures.append(f"{name}: quality below 0.6")
        if {slot.brand_name for slot in generated.spec.slots} != {
            kits[brand].name for brand in brands
        }:
            failures.append(f"{name}: co-brand attribution changed")
        if any(
            slot.logo_url != kits[brand].logo_url
            for slot in generated.spec.slots
            for brand in brands
            if slot.brand_name == kits[brand].name
        ):
            failures.append(f"{name}: logo redrawn or replaced")
        if any(slot.hero_image_url is None for slot in generated.spec.slots):
            failures.append(f"{name}: missing photography")
        if (generated.spec.video_url is not None) != (media == "video"):
            failures.append(f"{name}: wrong media format")
        if generated.spec.redemption_code is not None or generated.spec.qr_url is not None:
            failures.append(f"{name}: draft issued a live redemption")
    for left, right in product(catalog.brands, repeat=2):
        if sorted([left.slug, right.slug]) in catalog.approved_baskets:
            continue
        try:
            generator.generate(CampaignGenerate(brands=[left.slug, right.slug]))
        except ValueError:
            blocked += 1
        else:
            failures.append(f"{left.slug}+{right.slug}: unapproved or duplicate brands accepted")
    for photo in sorted((CAMPAIGN_ASSETS / "photos").glob("*.webp")):
        with Image.open(photo) as image:
            if (
                image.width < 1280
                or image.width * 9 != image.height * 16
                or photo.stat().st_size > 400_000
            ):
                failures.append(f"{photo.name}: photo dimensions or transfer budget")
    for video in sorted((CAMPAIGN_ASSETS / "videos").glob("*.mp4")):
        data = video.read_bytes()
        if data[4:8] != b"ftyp" or len(data) > 1_000_000 or b"avc1" not in data:
            failures.append(f"{video.name}: invalid or oversized H.264 video")
    p95 = sorted(times)[int((len(times) - 1) * 0.95)]
    if gate_latency and p95 > 5:
        failures.append(f"hot generation p95 {p95:.3f} ms exceeds 5 ms")
    if cached != len(times):
        failures.append("cache returned a changed or uncached campaign")
    return {
        "passed": not failures,
        "campaigns": len(times),
        "rejections": blocked,
        "cache_hit_rate": cached / len(times),
        "quality_mean": round(mean(qualities), 4),
        "quality_min": min(qualities),
        "hot_generate_p95_ms": round(p95, 3),
        "photo_assets": len(list((CAMPAIGN_ASSETS / "photos").glob("*.webp"))),
        "video_assets": len(list((CAMPAIGN_ASSETS / "videos").glob("*.mp4"))),
        "failures": failures,
    }


def main(out: Path) -> int:
    report = evaluate_campaigns()
    out.mkdir(parents=True, exist_ok=True)
    (out / "campaigns-latest.json").write_text(json.dumps(report, indent=2))
    rows = "\n".join(f"| {key} | {value} |" for key, value in report.items() if key != "failures")
    text = f"# Campaign generator evaluation\n\n**{'PASS' if report['passed'] else 'FAIL'}**\n\n| Metric | Value |\n|---|---|\n{rows}\n\n"
    text += "\n".join(f"- {failure}" for failure in report["failures"])
    (out / "campaigns-latest.md").write_text(text)
    print(text)
    return int(not report["passed"])
