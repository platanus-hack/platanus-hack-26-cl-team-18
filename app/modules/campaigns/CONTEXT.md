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
