"""Cold bundle-loop composition. Explicit budget; no generation at live startup."""

from decimal import Decimal

from app.bootstrap.demo import _slug, seed_market
from app.bootstrap.demo_catalog import catalog_named
from app.config.settings import AppSettings
from app.modules.common.application.id_generator import IdGenerator
from app.modules.creative.application.loops import BundleLoopLibrary, LoopJob, LoopReport
from app.modules.creative.infrastructure.fal_video import FalVideoGenerator
from app.modules.creative.infrastructure.h264_video import H264VideoGenerator


async def generate_seed_loops(
    settings: AppSettings,
    *,
    budget_usd: Decimal,
    cost_per_clip_usd: Decimal,
) -> LoopReport:
    if not settings.fal_key or not settings.fal_video_model:
        raise ValueError("Set FAL_KEY and FAL_VIDEO_MODEL before generating loops")
    if budget_usd <= 0 or cost_per_clip_usd <= 0:
        raise ValueError("Budget and maximum per-clip cost must be positive")
    import shutil

    if shutil.which("ffmpeg") is None:
        raise ValueError("Install ffmpeg before generating loops")
    market = seed_market(
        ids=IdGenerator(),
        budget_clp=settings.demo_budget_clp,
        catalog=catalog_named(settings.demo_catalog),
    )
    slugs = {kit.brand_id: _slug(kit.name) for kit in market.kits}
    categories = {bidder.brand_id: bidder.category for bidder in market.bidders}
    bundles = {frozenset({brand}) for brand in slugs} | set(market.offers.baskets())
    jobs = [
        LoopJob(
            slugs=tuple(slugs[b] for b in sorted(bundle, key=str)),
            categories=tuple(categories[b] for b in sorted(bundle, key=str)),
        )
        for bundle in bundles
    ]
    adapter = FalVideoGenerator(settings.fal_key.get_secret_value(), model=settings.fal_video_model)
    try:
        return await BundleLoopLibrary(settings.media_dir).generate_missing(
            H264VideoGenerator(adapter),
            sorted(jobs, key=lambda job: job.key),
            cost_per_clip_usd=cost_per_clip_usd,
            budget_usd=budget_usd,
            record_attempts=True,
        )
    finally:
        await adapter.aclose()
