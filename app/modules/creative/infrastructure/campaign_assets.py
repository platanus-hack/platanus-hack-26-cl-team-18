"""Load bundled campaign media once. All hot-path lookups use the returned mapping."""

import json
from pathlib import Path

from app.modules.creative.application.campaigns import CampaignMedia

CAMPAIGN_ASSETS = Path(__file__).resolve().parents[4] / "assets" / "campaigns"


def load_campaign_media(root: Path = CAMPAIGN_ASSETS) -> dict[str, CampaignMedia]:
    manifest = json.loads((root / "manifest.json").read_text())
    media = {}
    for key, entry in manifest["campaigns"].items():
        photo, video = entry["photo"], entry["video"]
        if any(not name.replace("-", "").isalnum() for name in (photo, video)):
            raise ValueError("Campaign assets must use local filenames")
        if not (root / "photos" / f"{photo}.webp").is_file():
            continue
        media[key] = CampaignMedia(
            photo_url=f"/campaign-assets/photos/{photo}.webp",
            video_url=f"/campaign-assets/videos/{video}.mp4"
            if (root / "videos" / f"{video}.mp4").is_file()
            else None,
        )
    return media
