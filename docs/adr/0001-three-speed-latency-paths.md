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
