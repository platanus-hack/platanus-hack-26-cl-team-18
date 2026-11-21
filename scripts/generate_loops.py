"""Generate approved bundle/solo loops once. Requires explicit provider pricing and budget."""

import argparse
import asyncio
import json
from dataclasses import asdict
from decimal import Decimal, InvalidOperation

from app.bootstrap.media import generate_seed_loops
from app.config.settings import AppSettings


def positive_decimal(raw: str) -> Decimal:
    try:
        value = Decimal(raw)
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError("must be a positive finite decimal") from exc
    if not value.is_finite() or value <= 0:
        raise argparse.ArgumentTypeError("must be a positive finite decimal")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--budget-usd", type=positive_decimal, required=True)
    parser.add_argument(
        "--cost-per-clip-usd",
        type=positive_decimal,
        required=True,
        help="Maximum charge per request for the configured provider model",
    )
    args = parser.parse_args()
    try:
        report = asyncio.run(
            generate_seed_loops(
                AppSettings(),
                budget_usd=args.budget_usd,
                cost_per_clip_usd=args.cost_per_clip_usd,
            )
        )
    except ValueError as exc:
        print(str(exc))
        return 2
    print(json.dumps(asdict(report), default=str, indent=2))
    return int(bool(report.failed or report.skipped_budget))


if __name__ == "__main__":
    raise SystemExit(main())
