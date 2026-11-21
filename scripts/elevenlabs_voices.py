"""List ElevenLabs voices verified for Spanish, to pick ELEVENLABS_NARRATOR_VOICE_ID.

    uv run python -m scripts.elevenlabs_voices [--all] [--search mujer]

Reads ELEVENLABS_API_KEY from the environment or .env. Prints voice ids and
labels only; never the key.
"""

from __future__ import annotations

import argparse
import sys

import httpx

from app.config.settings import AppSettings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--all", action="store_true", help="include voices without verified Spanish"
    )
    parser.add_argument("--search", default=None)
    args = parser.parse_args()

    settings = AppSettings()
    if not settings.elevenlabs_api_key:
        print("ELEVENLABS_API_KEY is not set (.env)", file=sys.stderr)
        return 2
    params: dict[str, str | int] = {"page_size": 100, "include_total_count": "false"}
    if args.search:
        params["search"] = args.search
    resp = httpx.get(
        f"{settings.elevenlabs_base_url}/v2/voices",
        params=params,
        headers={"xi-api-key": settings.elevenlabs_api_key.get_secret_value()},
        timeout=20,
    )
    if resp.status_code != 200:
        print(f"ElevenLabs answered HTTP {resp.status_code}", file=sys.stderr)
        return 1
    rows = []
    for voice in resp.json().get("voices", []):
        spanish = [
            v
            for v in voice.get("verified_languages") or []
            if str(v.get("language", "")).startswith("es")
        ]
        if not spanish and not args.all:
            continue
        labels = voice.get("labels") or {}
        accents = (
            ",".join(sorted({str(v.get("accent") or v.get("locale") or "es") for v in spanish}))
            or "-"
        )
        rows.append(
            (
                voice["voice_id"],
                voice.get("name", "?"),
                voice.get("category", "?"),
                labels.get("gender", "-"),
                accents,
            )
        )
    if not rows:
        print("no Spanish-verified voices found; try --all or add one from the Voice Library")
        return 1
    print(f"{'voice_id':<24} {'name':<28} {'category':<12} {'gender':<8} spanish accents")
    for row in rows:
        print(f"{row[0]:<24} {row[1][:27]:<28} {row[2]:<12} {row[3]:<8} {row[4]}")
    print("\nSet one in .env: ELEVENLABS_NARRATOR_VOICE_ID=<voice_id>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
