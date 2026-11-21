# Campaigns

A Campaign owns buying intent and bids in `Money` per attention-second. This
context owns advertisers, brands, categories, approved brand kits, schedules,
budgets and co-brand preferences. It supplies the auction with an in-memory
campaign book and supplies creative composition with approved brand kits.

- `application/live_book.py` (`LiveCampaignBook`): the hot read for auctions. It
  holds the live multiplier for the bidding war, idempotent by `(campaign, seq)`,
  and the budget accounts.
- `domain/budget.py` (`BudgetAccount`): a budget, what has been spent, and
  per-play reservations with explicit `reserve`/`settle`/`release`/`expire`
  transitions. Bidders see the budget net of spend and holds.
- `application/stock.py` (`StockBoard`): per-screen stock flags from `/clerk`.
  An out-of-stock brand is ineligible.
- `application/ports/registry.py` (`CampaignRegistry`): what outlives a deploy.
  The catalog (brands, rates, partners, offers) is code; the registry keeps one
  stable `CampaignId`/`BrandId` per brand slug, the budget, and the last live
  multiplier + seq. Spend is derived from the charge ledger at startup, never
  stored. Postgres (`campaign` table) when `DATABASE_URL` is set, in memory
  otherwise; read once at boot, multiplier changes written behind. A database
  down at boot falls back to in-memory campaigns (logged), so the screen still
  plays. Renaming a brand slug opens a new campaign: keep slugs stable.
  Budgets: `make campaign-budgets`; top up after rehearsals with
  `scripts/campaign_budget.py top-up all 50000`, then restart.
