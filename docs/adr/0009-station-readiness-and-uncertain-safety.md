# 0009: Persistent station records, reachable QR codes and conservative safety

- Status: accepted
- Date: 2026-11-22

The demo audit found loopback QR URLs, absent category loops, volatile station
records, 32 unsupported-prop no-fills, and child windows with no usable pose cues.

The station listens on the venue LAN, chooses its routed LAN address for QR codes,
and preserves an explicit tunnel URL. A loopback configuration is upgraded; no
usable address is an actionable startup failure. Device tokens are generated if
absent and passed to the local camera/player without printing their values. A
venue that isolates clients still needs the documented tunnel; a LAN address is
not proof of reachability from an isolated phone.

`make up` and `make station` prepare and migrate local PostgreSQL by default;
configuration of another database is preserved. A new station stops if its durable
prerequisites fail. Direct backend startup retains the existing explicit
`memory_fallback` behavior so an outage does not blank a running screen.
Issued offers, redemptions, charges and receipt facts survive restart. Each QR is
committed before publication; playback charges and receipt facts commit together
before acknowledgment. The auction remains in memory and model calls remain warm.
Local offer commits are included in the existing ready-at latency trace.

Cold startup builds missing H.264 loops from approved unbranded category footage
or category/brand photography, with separate panes for partners and cyclic camera
motion for still photos. Existing loops are preserved, and a failed encode never
publishes a partial file. No fal spend or generated logo is required. Prices,
claims, logos and QR remain player overlays.

GroupDefault receives the actual auction's eligibility result. A watching group
with no eligible/reserve-clearing offer retains its observed segments and gains
a low-confidence coffee fallback. It never bypasses stock, budget, partner, reserve
or minor-safety rules; a lone passer-by receives no group default. Weak single-
viewer affinity probes remain separate from group-fallback scenarios.

A missing pose cue is not negative evidence of a child. The wire contract now
carries aggregate `minor_likely.uncertain` separately from a positive detection.
Both block restricted categories, but neither targets. Unknown safety and a newly
confirmed minor publish immediately; clearing still requires stability. This does
not claim that pose geometry identifies every child. On the labelled clip set,
conservative protection rises from 34% to 100% of scored child time; confirmed
pose recall remains 26.4% with three detection-missed windows. Uncertainty blocks
about 66% of scored adult time too. This is an explicit safety/availability tradeoff.

The minor scorer now samples at a fixed 5 Hz on clip time rather than counting
change-triggered exports. Extra uncertainty messages otherwise bias its denominator.
Both saved pre-change recordings and fresh recordings were rescored identically:
confirmed recall 0.264 and adult false-positive rate 0.235955 before and after.
The old export-weighted values (0.30 / 0.270588) are not comparable; the baseline
was recalibrated from the saved **pre-change** data before judging the new run.
New absolute gates require full child-window protection and reject missing files
or empty windows. No detection threshold or safety bound was loosened.
