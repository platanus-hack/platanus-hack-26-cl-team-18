# Auction

An ad opportunity combines the published `AudienceMomentSnapshot`, the
campaign book's `Bidder`s (rates are `MoneyRate` per attention-second) and the
screen's facts: layouts, reserve, approved baskets and stock. The [local simulator](../../../docs/simulator.md)
assembles those inputs and calls the deterministic bundle auction in process.

The auction owns competitor exclusion, the block on age-restricted categories
when `minor_likely` is true, bundle selection, VCG clearing prices and recorded
exclusions. A cleared result captures winners, layout, prices and the
single-brand counterfactual. A no-fill result lets creative resolve the house ad.
See the [bundle auction rules](../../../docs/architecture.md#6-the-bundle-auction-precise-enough-to-implement)
and the [module build plan](../../../docs/auction-plan.md) (requirements R-*, slices S1–S9).

Code map: `domain/clearing.py` (`clear`) is the pure auction, and
`domain/swap.py` is the minimum-ad-seconds and hysteresis policy.
`application/ports/engine.py` is the `AuctionEngine` seam,
`infrastructure/engines/in_process.py` is the default engine, and
`application/desk.py` is the per-screen desk that decides if and when the
screen swaps.
