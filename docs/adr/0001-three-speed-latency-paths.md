# 0001: Three-speed latency paths (hot / warm / cold)

- Status: accepted
- Date: 2026-11-21

## Context

The product promise is "the ad changes for the people in front of the screen,
now". The slow pieces (VLM tagging, LLM copy, image generation) take 0.5 to 4 s
each and can fail. If any of them sits in series between perception and
display, glass-to-glass latency becomes 5 to 10 s and fails unpredictably.

## Decision

Split work into three paths (see `docs/architecture.md` §1):

- **Hot:** device perception, moment change detection, auction, creative cache
  lookup or template creative, and the swap. No network model calls. Target
  under 300 ms server side plus the device debounce.
- **Warm:** VLM tagging, AI copy, hero images. Asynchronous; results *upgrade*
  the moment or the creative in place.
- **Cold:** brand-kit prep, pre-rendered creatives for likely bundles, video
  loops.

Hot-path code must never `await` a warm-path result.

## Consequences

- Creatives must be renderable from a template plus stock copy alone.
- We need a creative cache and a `CreativeUpgraded` message.
- Moments can be refined after an auction has run; a refinement that changes
  the winner triggers a new auction (bounded by a minimum dwell per creative so
  the screen doesn't flicker).

## Amendment (2026-11-21): optional media never gates visual readiness

The browser was awaiting optional hero images, video `canplaythrough` and
voice preparation after the required logos decoded. A stalled optional
asset added up to 3 seconds to the swap, even though the hot backend decision
was already ready in milliseconds.

Only real brand logos gate the visual swap. Decode each distinct logo URL,
then crossfade the copy, approved price and logos over the last good ad.
Video and hero images load in the mounted layer. The actual video stays
hidden until its own `canplaythrough`; a failed video hides again. Voice
preparation is independent: cached audio plays when the ad is on air, late audio plays only while that same creative/voice remains current,
and replacement cancels preparation and stops the old audio. Optional media
can therefore begin after the visual swap. A failed or stalled required logo
still retains the previous creative.

This refines the rendering readiness rule in `docs/stack.md`; it changes no
stack component. Deterministic player tests lock down the display budget and
late/stale voice behavior. Measurements are in `docs/creative-generation.md`
§8; camera-to-TV latency remains a separate hardware rehearsal measurement.

## Amendment: isolate outbound clients and shared voice consumers

A controlled slow-console probe showed a healthy console waiting 361 ms behind
another client's 120 ms writes. Concurrent sends to one player also overlapped.
Preserve ordered batches per connection rather than holding a global console
lock. Bound pending batches per connection (32 by default); on overflow or a
send timeout, retire its pending writes and close with 1013 so it reconnects to
the current state. A healthy connection proceeds independently. The player
continues showing the last good creative while reconnecting.

A voice result shared across screens must not reserve an additional provider
slot per waiting screen. Screens subscribe to the existing synthesis and apply
the result only while their current line and voice match. Unique syntheses
retain provider admission limits, budget checks and single-flight caching.
This changes neither the stack nor first-visual readiness. Measurements and
regressions are recorded in `docs/latency-feedback-loop.md` (second loop).
