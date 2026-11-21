"""Download the stock clips in clips.json into the gitignored videos directory."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
VIDEOS = HERE.parent / "perception" / "videos"
# Wikimedia's UA policy wants a URL in the agent string; without one it answers 429.
USER_AGENT = "team18-evals/0.1 (hackathon test fixtures; https://github.com/)"


def clip_path(clip: dict) -> Path:
    suffix = ".webm" if clip["url"].endswith(".webm") else ".mov"
    return VIDEOS / f"{clip['id']}{suffix}"


def load_clips() -> list[dict]:
    return list(json.loads((HERE / "clips.json").read_text(encoding="utf-8"))["clips"])


def _download(url: str, path: Path, *, attempts: int = 5) -> None:
    for attempt in range(attempts):
        with httpx.stream(
            "GET", url, headers={"User-Agent": USER_AGENT}, follow_redirects=True, timeout=120
        ) as response:
            if response.status_code == 429 and attempt < attempts - 1:
                wait = int(response.headers.get("Retry-After") or 10 * 2**attempt)
                print(f"rate limited, retrying in {wait}s")
                time.sleep(wait)
                continue
            response.raise_for_status()
            partial = path.with_suffix(path.suffix + ".part")
            with partial.open("wb") as sink:
                for chunk in response.iter_bytes():
                    sink.write(chunk)
            partial.replace(path)
            return


def main() -> int:
    VIDEOS.mkdir(parents=True, exist_ok=True)
    for clip in load_clips():
        path = clip_path(clip)
        if path.exists() and path.stat().st_size > 0:
            print(f"have {path.name}")
            continue
        _download(clip["url"], path)
        print(f"got  {path.name} ({path.stat().st_size / 1e6:.1f} MB, {clip['license']})")
        time.sleep(3)  # be polite: Wikimedia rate-limits bursts (HTTP 429)
    return 0


if __name__ == "__main__":
    sys.exit(main())
