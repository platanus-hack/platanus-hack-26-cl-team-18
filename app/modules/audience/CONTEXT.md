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
