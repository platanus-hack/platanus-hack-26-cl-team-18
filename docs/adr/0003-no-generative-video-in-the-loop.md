# 0003: No generative video in the real-time loop

- Status: accepted, amended 2026-11-21 (see "Amendment")
- Date: 2026-11-21

## Context

Generating video is the obvious "wow", but current video models take tens of
seconds to minutes per clip, cost a lot per second of output, and garble logos
and text.

## Decision

Real-time creatives are **composed**, not generated: a layout template, real
brand-kit assets (vector logos always overlaid, never generated), AI copy, and
motion from CSS, Lottie or PixiJS. Optional AI hero *images* arrive as a warm
upgrade. Video, if any, is pre-generated offline (cold path) and stored in the
brand kit as a loop.

## Consequences

- The creative looks alive at 60 fps with no generation latency.
- Brand safety is enforceable: what the brand approved is what is shown.
- When video models become fast enough, they plug in as another warm-path
  upgrade without changing the auction or the contract.

## Amendment (2026-11-21): fast video as cold loops plus warm upgrades

Turbo video models now report about 2 to 15 s per 5 s clip (vendor numbers,
verify on day 1). Video is still **never in the hot path**, but it is now a
first-class creative layer:

- **Cold:** at seed time, pre-generate a loopable clip for every compatible
  bundle (pairs plus demo trios) and a solo clip per brand, using
  image-to-video from a first frame we compose from the brand kits' product
  cutouts.
- **Warm:** a turbo model generates a missing bundle × mood loop at 480–768p
  and swaps it in when ready. Budget-capped and deduped by cache key.
- Logos, copy, QR codes and prices are always player overlays, never part of
  the generated video.

Details: `docs/architecture.md` §8.
