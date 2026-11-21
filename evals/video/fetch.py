"""Download the stock clips in clips.json into the gitignored videos directory."""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
VIDEOS = HERE.parent / "perception" / "videos"
USER_AGENT = "team18-evals/0.1 (hackathon test fixtures)"


def clip_path(clip: dict) -> Path:
    suffix = ".webm" if clip["url"].endswith(".webm") else ".mov"
    return VIDEOS / f"{clip['id']}{suffix}"


def load_clips() -> list[dict]:
    return list(json.loads((HERE / "clips.json").read_text(encoding="utf-8"))["clips"])


def _download(url: str, *, attempts: int = 6) -> bytes:
    for attempt in range(attempts):
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return bytes(response.read())
        except urllib.error.HTTPError as exc:
            if exc.code != 429 or attempt == attempts - 1:
                raise
            wait = int(exc.headers.get("Retry-After") or 10 * 2**attempt)
            print(f"rate limited, retrying in {wait}s")
            time.sleep(wait)
    raise RuntimeError("unreachable")


def main() -> int:
    VIDEOS.mkdir(parents=True, exist_ok=True)
    for clip in load_clips():
        path = clip_path(clip)
        if path.exists() and path.stat().st_size > 0:
            print(f"have {path.name}")
            continue
        path.write_bytes(_download(clip["url"]))
        print(f"got  {path.name} ({path.stat().st_size / 1e6:.1f} MB, {clip['license']})")
        time.sleep(3)  # be polite: Wikimedia rate-limits bursts (HTTP 429)
    return 0


if __name__ == "__main__":
    sys.exit(main())
