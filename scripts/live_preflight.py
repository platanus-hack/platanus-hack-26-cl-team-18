"""Inspect live-loop configuration without contacting or charging providers."""

import argparse
import asyncio
import json

from app.bootstrap.container import build_container
from app.bootstrap.readiness import live_readiness
from app.config.settings import AppSettings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--require-providers",
        action="store_true",
        help="Fail if any provider or loop is unavailable",
    )
    args = parser.parse_args()
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
