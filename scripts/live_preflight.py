"""Inspect live-loop configuration without contacting or charging providers.

Besides providers and media, it names what silently breaks a demo: a campaign a
rehearsal drained (budgets persist in Postgres), records that fell back to memory,
a QR origin phones can't open, a skewed agent clock and missing campaign photos.
Those print as WARNING lines and fail any `--require-*` gate.
"""

import argparse
import asyncio
import json
import sys
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from app.api.demo_access import DEMO_KEY_HEADER
from app.bootstrap.container import build_container
from app.bootstrap.readiness import live_readiness, records_status
from app.config.settings import AppSettings

# A 3-minute demo spends well under this per brand: the soak measured ~280 CLP per
# occupied minute at neutral bids, and the bidding war's max multiplier is x5.
DEMO_MIN_REMAINING_CLP = 5_000
# Agent and backend share the station laptop; beyond this the latency trace is
# wrong, and past the loop's 10 s skew bound every snapshot is dropped.
MAX_CLOCK_SKEW_S = 2.0


def live_input_failures(report: dict, screen: str, *, require_speech: bool = False) -> list[str]:
    """Check the running station, rather than this shell's provider configuration."""
    inputs = report.get("inputs", {}).get(screen, {})
    failures = []
    if not inputs.get("camera_recent"):
        failures.append(f"{screen}: no recent camera snapshots on this backend")
    skew = inputs.get("camera_clock_skew_s")
    if skew is not None and abs(skew) > MAX_CLOCK_SKEW_S:
        failures.append(
            f"{screen}: agent clock is {skew:+.1f}s off the backend's; sync the clocks "
            "(timedatectl) or snapshots are dropped past 10 s"
        )
    if screen not in report.get("screens_connected", []):
        failures.append(f"{screen}: no player connected to this backend")
    if require_speech:
        if not inputs.get("speech_enabled"):
            failures.append(f"{screen}: speech mode is disabled on this backend")
        elif not inputs.get("speech_recent"):
            failures.append(f"{screen}: no speech event received; say a demo phrase")
    return failures


def budget_failures(book: dict, minimum: int = DEMO_MIN_REMAINING_CLP) -> list[str]:
    """Campaigns with too little left for a demo (a ConsoleBook as JSON)."""
    failures = []
    for line in book.get("lines", []):
        try:
            remaining = Decimal(line["remaining"]["amount"])
        except KeyError, TypeError, InvalidOperation:
            continue
        if remaining < minimum:
            failures.append(
                f"{line.get('brand_name', '?')}: only {remaining:.0f} "
                f"{line['remaining'].get('currency', '')} left, it stops bidding mid-demo; "
                f"`make campaign-budgets` / scripts/campaign_budget.py top-up all 50000, "
                "then restart"
            )
    return failures


def health_failures(report: dict) -> list[str]:
    """Records and media problems in a /health-shaped report."""
    failures = []
    records = report.get("records") or {}
    if records.get("campaigns") == "memory_fallback":
        failures.append(
            "DATABASE_URL is set but Postgres was down at boot: budgets and bids are in "
            "memory this run (spend is not restored, charges are not kept); start "
            "Postgres and restart, or blank DATABASE_URL"
        )
    dropped = (records.get("writer") or {}).get("dropped") or 0
    if dropped:
        failures.append(f"{dropped} record rows were dropped (database unreachable)")
    missing = report.get("ads", {}).get("media", {}).get("missing_campaign_photos") or []
    if missing:
        failures.append(
            f"campaign photos missing from assets/campaigns/photos: {', '.join(missing)} "
            "(those campaigns air the template)"
        )
    return failures


def _fetch_json(url: str, headers: dict[str, str] | None = None) -> dict:
    with urlopen(Request(url, headers=headers or {}), timeout=3) as response:
        return json.load(response)


def _warn(warnings: list[str]) -> None:
    for warning in warnings:
        print(f"WARNING: {warning}", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
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
    gated = args.require_providers or args.require_inputs or args.require_speech
    if (args.require_inputs or args.require_speech) and not args.server:
        parser.error("--require-inputs/--require-speech needs --server (the actual player origin)")
    settings = AppSettings()
    if args.server:
        url = urlsplit(args.server)
        if url.scheme not in {"http", "https"} or not url.netloc:
            parser.error("--server must be an http(s) origin")
        origin = f"{url.scheme}://{url.netloc}"
        try:
            report = _fetch_json(f"{origin}/health")
        except OSError, ValueError:
            print(json.dumps({"server": origin, "error": "health endpoint unavailable"}))
            return 1
        failures = live_input_failures(report, args.screen, require_speech=args.require_speech)
        warnings = health_failures(report)
        key = settings.demo_key.get_secret_value() if settings.demo_key else ""
        if key:  # the book sits behind the demo key; never printed
            try:
                book = _fetch_json(
                    f"{origin}/api/book?screen_id={args.screen}", {DEMO_KEY_HEADER: key}
                )
                warnings += budget_failures(book)
            except OSError, ValueError:
                warnings.append("could not read campaign budgets (/api/book with DEMO_KEY)")
        print(
            json.dumps(
                {"server": origin, "failures": failures, "warnings": warnings, "health": report},
                indent=2,
            )
        )
        _warn(failures + warnings)
        if (args.require_inputs or args.require_speech) and failures:
            return 1
        if gated and warnings:
            return 1
        ads = report.get("ads", {})
        if args.require_providers and (
            any(not p["enabled"] or not p["configured"] for p in ads.get("providers", {}).values())
            or ads.get("media", {}).get("missing_loops")
        ):
            return 1
        return 0 if ads.get("enabled") else 1
    container = build_container(settings)
    try:
        report = live_readiness(container)
        warnings = settings.misconfigurations() + health_failures(
            {"records": records_status(container), "ads": report}
        )
        if container.demo is not None:
            warnings += budget_failures(container.demo.book_view().model_dump(mode="json"))
        print(json.dumps({**report, "warnings": warnings}, indent=2))
        _warn(warnings)
        if not container.settings.live_ads_enabled:
            return 1
        if gated and warnings:
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
