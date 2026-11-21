"""Inspect live-loop configuration without contacting or charging providers."""

import argparse
import asyncio
import json
from urllib.parse import urlsplit
from urllib.request import urlopen

from app.bootstrap.container import build_container
from app.bootstrap.readiness import live_readiness
from app.config.settings import AppSettings


def live_input_failures(report: dict, screen: str, *, require_speech: bool = False) -> list[str]:
    """Check the running station, rather than this shell's provider configuration."""
    inputs = report.get("inputs", {}).get(screen, {})
    failures = []
    if not inputs.get("camera_recent"):
        failures.append(f"{screen}: no recent camera snapshots on this backend")
    if screen not in report.get("screens_connected", []):
        failures.append(f"{screen}: no player connected to this backend")
    if require_speech:
        if not inputs.get("speech_enabled"):
            failures.append(f"{screen}: speech mode is disabled on this backend")
        elif not inputs.get("speech_recent"):
            failures.append(f"{screen}: no speech event received; say a demo phrase")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--require-providers",
        action="store_true",
        help="Fail if any provider or loop is unavailable",
    )
    parser.add_argument("--server", help="Running player origin, e.g. http://127.0.0.1:5188")
    parser.add_argument("--screen", default="demo-01")
    parser.add_argument("--require-inputs", action="store_true", help="Gate live camera + player")
    parser.add_argument(
        "--require-speech", action="store_true", help="Also require an opted-in speech event"
    )
    args = parser.parse_args()
    if (args.require_inputs or args.require_speech) and not args.server:
        parser.error("--require-inputs/--require-speech needs --server (the actual player origin)")
    if args.server:
        url = urlsplit(args.server)
        if url.scheme not in {"http", "https"} or not url.netloc:
            parser.error("--server must be an http(s) origin")
        origin = f"{url.scheme}://{url.netloc}"
        try:
            with urlopen(f"{origin}/health", timeout=3) as response:
                report = json.load(response)
        except OSError, ValueError:
            print(json.dumps({"server": origin, "error": "health endpoint unavailable"}))
            return 1
        failures = live_input_failures(report, args.screen, require_speech=args.require_speech)
        print(json.dumps({"server": origin, "failures": failures, "health": report}, indent=2))
        if (args.require_inputs or args.require_speech) and failures:
            return 1
        ads = report.get("ads", {})
        if args.require_providers and (
            any(not p["enabled"] or not p["configured"] for p in ads.get("providers", {}).values())
            or ads.get("media", {}).get("missing_loops")
        ):
            return 1
        return 0 if ads.get("enabled") else 1
    container = build_container(AppSettings())
    try:
        report = live_readiness(container)
        print(json.dumps(report, indent=2))
        if not container.settings.live_ads_enabled:
            return 1
        if args.require_providers:
            providers = report["providers"]
            assert isinstance(providers, dict)
            media = report["media"]
            assert isinstance(media, dict)
            if (
                any(not p["enabled"] or not p["configured"] for p in providers.values())
                or media["missing_loops"]
            ):
                return 1
        return 0
    finally:
        asyncio.run(container.aclose())


if __name__ == "__main__":
    raise SystemExit(main())
