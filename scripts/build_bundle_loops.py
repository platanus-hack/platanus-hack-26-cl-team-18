"""Compose a loop per approved bundle and per brand from approved campaign footage. Free, offline.

The paid path (`make bundle-loops`, fal) generates new footage. This script builds
the same files from the campaign videos we already own (assets/campaigns/videos):
a solo loop is the brand category's clip graded toward the brand's accent colour;
a bundle loop puts each member's graded clip side by side, in basket order.
So Rayo + Café Patio no longer borrows Voltio's energy-coffee clip.

Loops carry no logos, text or prices (ADR 0003): the player overlays those.
The output is the BundleLoopLibrary layout (media_dir/loops/<a>+<b>.mp4), so the
player picks loops up with no code change. Existing files are kept unless
--force, so a fal-generated loop is never overwritten by a composed one.

    uv run --with imageio-ffmpeg python -m scripts.build_bundle_loops
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from app.bootstrap.demo import _slug, seed_market
from app.bootstrap.demo_catalog import catalog_named
from app.config.settings import AppSettings
from app.modules.common.application.id_generator import IdGenerator
from app.modules.creative.application.campaigns import CampaignMedia, media_for
from app.modules.creative.application.loops import LOOP_DIR, bundle_key
from app.modules.creative.infrastructure.campaign_assets import CAMPAIGN_ASSETS

ASSETS = CAMPAIGN_ASSETS
VIDEOS = ASSETS / "videos"
# Real, credited campaign photography (assets/campaigns/photos/SOURCES.md).
REAL_PHOTO_PREFIX = "cl-"
CLIP_BY_CATEGORY = {
    "sports_goods": "energy.mp4",
    "energy_drink": "energy.mp4",
    "coffee": "coffee.mp4",
    "alcohol": "beer.mp4",
}
# Where the product sits across each clip (0 = left edge): panes crop around it.
FOCUS_X = {"energy.mp4": 0.80, "coffee.mp4": 0.78, "beer.mp4": 0.86}
WIDTH, HEIGHT, SECONDS = 1280, 720, 6
GRADE_OPACITY = "0.28"  # enough to tell Voltio's lime from Rayo's violet; footage stays real


def source_for(slug: str, category: str, media: dict[str, CampaignMedia]) -> Path:
    """Only approved footage or the brand/category's approved photography."""
    # A brand with its own real campaign photo (the credited `cl-` shots: real cans,
    # real food, real Chilean roads; never AI) loops that photo. The category clips below
    # are the fictional catalog's AI scenery: a Chilean energy drink or coffee looping
    # them read as fake on the TV ("AI vibecoded"). The logo stays a player overlay.
    own = media_for(media, ((slug, category),))
    if own is not None and own[1].photo_url:
        photo = own[1].photo_url
        if photo.rsplit("/", 1)[-1].startswith(REAL_PHOTO_PREFIX):
            source = ASSETS / photo.removeprefix("/campaign-assets/")
            if source.is_file():
                return source
    # Category scenes are explicitly unbranded.
    if category in CLIP_BY_CATEGORY:
        source = VIDEOS / CLIP_BY_CATEGORY[category]
        if source.is_file():
            return source
    category_media = media.get(f"category:{category}")
    found = (category, category_media) if category_media else media_for(media, ((slug, category),))
    if found is not None:
        campaign = found[1]
        url = campaign.video_url or campaign.photo_url
        source = ASSETS / url.removeprefix("/campaign-assets/")
        if source.is_file():
            return source
    if category in CLIP_BY_CATEGORY:
        source = VIDEOS / CLIP_BY_CATEGORY[category]
        if source.is_file():
            return source
    raise ValueError(f"No approved loop source for {slug} ({category})")


def ffmpeg_exe() -> str:
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg  # type: ignore[import-not-found]
    except ImportError:
        sys.exit("ffmpeg not found: install it or run with `uv run --with imageio-ffmpeg`")
    return str(imageio_ffmpeg.get_ffmpeg_exe())


def compose_command(ffmpeg: str, members: list[tuple[Path, str]], out: Path) -> list[str]:
    """members: (clip, accent '#rrggbb') in basket order → one 1280×720 H.264 loop."""
    pane_w = WIDTH // len(members)
    inputs: list[str] = []
    filters: list[str] = []
    for i, (clip, accent) in enumerate(members):
        photo = clip.suffix.lower() in {".webp", ".png", ".jpg", ".jpeg"}
        inputs += ["-loop", "1", "-framerate", "24"] if photo else ["-stream_loop", "-1"]
        inputs += ["-t", str(SECONDS), "-i", str(clip)]
        inputs += [
            "-f",
            "lavfi",
            "-t",
            str(SECONDS),
            "-i",
            f"color=c=0x{accent.lstrip('#')}:s={pane_w}x{HEIGHT}:r=24",
        ]
        motion = (
            f"zoompan=z='1.04+0.04*sin(2*PI*on/{SECONDS * 24 - 1})':"
            f"x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':d=1:s={pane_w}x{HEIGHT}:fps=24,"
            if photo
            else ""
        )
        filters.append(
            f"[{2 * i}:v]scale=-2:{HEIGHT},"
            f"crop={pane_w}:{HEIGHT}:'min(max(iw*{FOCUS_X.get(clip.name, 0.5)}-{pane_w}/2,0),iw-{pane_w})':0,"
            f"fps=24,{motion}format=yuv420p[v{i}];"
            f"[{2 * i + 1}:v]format=yuv420p[c{i}];"
            f"[v{i}][c{i}]blend=all_mode=softlight:all_opacity={GRADE_OPACITY}[g{i}]"
        )
    panes = "".join(f"[g{i}]" for i in range(len(members)))
    stack = f"{panes}hstack=inputs={len(members)}[out]" if len(members) > 1 else "[g0]null[out]"
    return [
        ffmpeg, "-y", "-loglevel", "error", "-filter_complex_threads", "1", *inputs,
        "-filter_complex", ";".join(filters) + ";" + stack,
        "-map", "[out]", "-t", str(SECONDS), "-an",
        "-c:v", "libx264", "-profile:v", "high", "-pix_fmt", "yuv420p",
        "-preset", "veryfast", "-threads", "2", "-crf", "23", "-movflags", "+faststart",
        str(out),
    ]  # fmt: skip


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--media-dir", type=Path, default=None, help="default: settings.media_dir")
    parser.add_argument("--force", action="store_true", help="rebuild loops that already exist")
    args = parser.parse_args()
    settings = AppSettings()
    media_dir: Path = args.media_dir or settings.media_dir
    market = seed_market(
        ids=IdGenerator(),
        budget_clp=settings.demo_budget_clp,
        catalog=catalog_named(settings.demo_catalog),
    )
    kits = {kit.brand_id: kit for kit in market.kits}
    category = {bidder.brand_id: bidder.category for bidder in market.bidders}
    bundles = {frozenset({brand}) for brand in kits} | set(market.offers.baskets())
    out_dir = media_dir / LOOP_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    ffmpeg = ffmpeg_exe()
    built: list[str] = []
    kept: list[str] = []
    no_footage: list[str] = []
    for bundle in sorted(bundles, key=lambda b: sorted(_slug(kits[x].name) for x in b)):
        # Basket order = BundleLoopLibrary key order (sorted slugs), left to right.
        brands = sorted(bundle, key=lambda b: _slug(kits[b].name))
        key = bundle_key([_slug(kits[b].name) for b in brands])
        out = out_dir / f"{key}.mp4"
        if out.is_file() and not args.force:
            kept.append(key)
            continue
        try:
            members = [
                (
                    source_for(_slug(kits[b].name), category[b], market.campaign_media),
                    kits[b].accent_color or "#ffffff",
                )
                for b in brands
            ]
        except ValueError:
            no_footage.append(key)
            continue
        temporary = out.with_suffix(".building.mp4")
        try:
            subprocess.run(compose_command(ffmpeg, members, temporary), check=True)
            temporary.replace(out)
        finally:
            temporary.unlink(missing_ok=True)
        built.append(key)
    print(f"built {len(built)}: {', '.join(built) or '—'}")
    print(f"kept {len(kept)} existing: {', '.join(kept) or '—'}")
    print(f"no footage {len(no_footage)}: {', '.join(no_footage) or '—'} (player uses brand media)")
    print(f"→ {out_dir}")
    return 1 if no_footage else 0


if __name__ == "__main__":
    raise SystemExit(main())
