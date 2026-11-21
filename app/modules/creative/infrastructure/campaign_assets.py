"""Load bundled campaign media once. All hot-path lookups use the returned mapping."""

import json
from pathlib import Path

from app.modules.creative.application.campaigns import CampaignMedia

CAMPAIGN_ASSETS = Path(__file__).resolve().parents[4] / "assets" / "campaigns"


def _local(name: str) -> str:
    if not name.replace("-", "").isalnum():
        raise ValueError("Campaign assets must use local filenames")
    return name


def load_campaign_media(root: Path = CAMPAIGN_ASSETS) -> dict[str, CampaignMedia]:
    """Manifest keys: a basket ("juan-valdez+score"), a brand ("score") or a category
    scene ("category:ice_cream"). Each has a primary `photo`, optional `alternates`
    aired in rotation, and an optional `video` loop made from the primary photo."""
    manifest = json.loads((root / "manifest.json").read_text())
    media = {}
    for key, entry in manifest["campaigns"].items():
        photo, video = _local(entry["photo"]), _local(entry["video"])
        alternates = tuple(_local(name) for name in entry.get("alternates", ()))
        if not (root / "photos" / f"{photo}.webp").is_file():
            continue
        media[key] = CampaignMedia(
            photo_url=f"/campaign-assets/photos/{photo}.webp",
            video_url=f"/campaign-assets/videos/{video}.mp4"
            if (root / "videos" / f"{video}.mp4").is_file()
            else None,
            alternates=tuple(
                f"/campaign-assets/photos/{name}.webp"
                for name in alternates
                if (root / "photos" / f"{name}.webp").is_file() and name != photo
            ),
        )
    return media
