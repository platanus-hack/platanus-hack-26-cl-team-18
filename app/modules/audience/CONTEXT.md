# Audience

The perception agent sends audience snapshots, face-blurred keyframes and
ephemeral utterances over the existing WebSocket contract. Audience owns IAB
classification, topic expiry and the rules that determine when observations
form a new stable audience moment.

In the hot path, map recognized objects and sound-scene cues through the approved
segment catalog and debounce moment changes. In the warm path, `AudienceTagger`
accepts face-blurred keyframes and `TopicExtractor` accepts an utterance from an
opted-in screen. Keep inputs in memory and discard utterances after extraction.
Expose only classified topics to the console. Merge valid results
only into their current moment. Expire speech evidence before publishing the
next snapshot, including removing the speech source and intent when the same
segment still has visual evidence.

Publish an `AudienceMomentSnapshot` with `AudienceMomentChanged`. It carries
screen and moment IDs, measured/received/moment-changed instants, counts,
block-only `minor_likely`, and one `SegmentEstimate` per segment. Each estimate
counts looking viewers and carries confidence and its contributing sources.
Counts may overlap across segments. This is an internal value object. Only the
tagging adapter resolves IDs against the approved taxonomy catalog. Use
`Percentage` for confidence and intent in the domain. A wire score of 0.8
maps to 80 percent. Normalize timestamps to UTC before constructing the value
objects.

## Classifier (implemented)

`application/classifier.py` (`AudienceClassifier`) turns one snapshot into
segments; the rules are pure (`domain/classification.py`,
`domain/policies/fusion.py`).

- **Catalog**: `infrastructure/catalog/iab_audience_1_1_cstore.json`, a subset
  of the IAB Audience Taxonomy 1.1 (Interest and Purchase Intent only, IDs
  copied from the official TSV), vision and sound cue rules, and the products
  the VLM may name. Brand refs are the prop marker slugs
  (`edge/props/markers.json`). Cues not in the catalog never reach the auction.
- **Fusion**: strongest claim within a source, noisy-OR across sources
  (vision, sound, vlm), viewer count capped by `looking`. Weak segments still
  bid by default: the auction prices confidence and records `below_reserve`.
- **Held products**: `product:<slug>` markers (ADR 0008 D2) and the VLM's label
  reading; the demo maps slugs to `BrandId`s for `AdOpportunity.held_products`.
- **Warm path**: `ClaudeAudienceTagger` tags face-blurred keyframes with
  catalog-enumerated structured output (no field for any personal attribute).
  Findings last ~6 s; `minor_likely` from either source blocks. Failures flag
  `vlm_unavailable` and the hot path continues.
- **Not here by design**: moment debounce (the agent, ADR 0008 D10, plus the
  swap hold in `auction/domain/swap.py`) and speech (`domain/speech_evidence.py`).
