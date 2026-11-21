# Auction

An ad opportunity combines the published `AudienceMomentSnapshot`, the
campaign book and the screen's supported layouts. The [local simulator](../../../docs/simulator.md)
assembles those inputs and calls the deterministic bundle auction in process.

The auction owns competitor exclusion, the block on age-restricted categories
when `minor_likely` is true, bundle selection, VCG clearing prices and recorded
exclusions. A cleared result captures winners, layout, prices and the
single-brand counterfactual. A no-fill result lets creative resolve the house ad.
See the [bundle auction rules](../../../docs/architecture.md#6-the-bundle-auction-precise-enough-to-implement).
