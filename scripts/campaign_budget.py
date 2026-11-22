"""Show campaign budgets, or give campaigns money to spend again (after rehearsals).

    uv run python scripts/campaign_budget.py status
    uv run python scripts/campaign_budget.py top-up all 50000        # every campaign
    uv run python scripts/campaign_budget.py top-up cafe-patio 20000 # one brand slug

`top-up` sets the budget to spent + AMOUNT, so each campaign has exactly AMOUNT CLP
left. Spend is never edited: it is the append-only charge ledger. The running app
reads budgets at startup, so restart it (or redeploy) for a top-up to take effect.
Reads DATABASE_URL from the environment or .env (`make tf-output OUT=database_url_external` for Render).
"""

from __future__ import annotations

import argparse
import os
import sys
from decimal import Decimal
from pathlib import Path

SPENT = """
SELECT c.brand_slug, c.currency, c.budget, COALESCE(SUM(ch.amount), 0), c.live_multiplier
FROM campaign c LEFT JOIN charge ch ON ch.campaign_id = c.campaign_id
WHERE %s = 'all' OR c.brand_slug = %s
GROUP BY c.campaign_id ORDER BY c.brand_slug
"""

TOP_UP = """
UPDATE campaign c SET budget = spent.total + %s, updated_at = now()
FROM (
    SELECT c2.campaign_id, COALESCE(SUM(ch.amount), 0) AS total
    FROM campaign c2 LEFT JOIN charge ch ON ch.campaign_id = c2.campaign_id
    GROUP BY c2.campaign_id
) AS spent
WHERE c.campaign_id = spent.campaign_id AND (%s = 'all' OR c.brand_slug = %s)
"""


def _connect():  # type: ignore[no-untyped-def]  # pragma: no cover - needs a database
    import psycopg2

    url = os.environ.get("DATABASE_URL")
    if not url:
        # Same source as the station: .env through AppSettings (run as a plain script too).
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from app.config.settings import AppSettings

        secret = AppSettings().database_url
        url = secret.get_secret_value() if secret else None
    if not url:
        raise SystemExit("ERROR: DATABASE_URL is not set (env or .env).")
    return psycopg2.connect(url, connect_timeout=5)


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - needs a database
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    status = sub.add_parser("status")
    status.add_argument("slug", nargs="?", default="all")
    top_up = sub.add_parser("top-up")
    top_up.add_argument("slug", help="a brand slug, or 'all'")
    top_up.add_argument("amount", type=Decimal, help="CLP left to spend after the top-up")
    args = parser.parse_args(argv)

    conn = _connect()
    try:
        with conn, conn.cursor() as cur:
            if args.command == "top-up":
                if args.amount < 0:
                    raise SystemExit("ERROR: amount must be >= 0")
                cur.execute(TOP_UP, (args.amount, args.slug, args.slug))
                if cur.rowcount == 0:
                    raise SystemExit(f"ERROR: no campaign matches {args.slug!r}")
                print(f"Topped up {cur.rowcount} campaign(s). Restart the app to apply.")
            cur.execute(SPENT, (args.slug, args.slug))
            print(f"{'slug':<20} {'budget':>12} {'spent':>12} {'left':>12} {'bid x':>6}")
            for slug, currency, budget, spent, multiplier in cur.fetchall():
                left = max(budget - spent, Decimal(0))
                print(
                    f"{slug:<20} {budget:>12.0f} {spent:>12.0f} {left:>12.0f} "
                    f"{multiplier:>6.2f} {currency.strip()}"
                )
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
