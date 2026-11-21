# Sensing-to-ad latency feedback loop

The audit identified stacked temporal filters, cold fallback inference,
frame-count sampling, synchronous face-blurred keyframe encoding, reconnect
backlogs and late voice fetching. Auction/creative processing was already a
small part of the hot path. This change addresses those causes without putting
model calls into first-display readiness.

## Hypotheses and results

Controlled trials use `scripts/bench_sensing.py`: a 30 Hz synthetic source,
80 ms simulated detection, the real Python 3.12 tracker/snapshot/link, real
backend WebSockets and Chromium's actual `player.displayed` report. They use
stock copy, with keyframes and live providers disabled. These are individual
observations on a shared development host, not production percentiles.

| Measurement | Before | After |
|---|---:|---:|
| Look-away confirmation frame | 1,000 ms | 467–533 ms |
| Departure confirmation frame | 1,433 ms | 733–900 ms |
| Latest scene-to-visible trial | 1,002 ms | 967 ms |
| Reconnect fresh-state delivery (64 states, 10 ms/send) | 656 ms | 10.5 ms |
| Trace coverage of first scene confirmation | omitted ~600 ms | included |

Arrival retains the existing 500 ms debounce; these trials do not establish a
first-arrival speedup. The final trial's backend stage was 23 ms, with zero
uncaught browser errors. Browser visibility includes the existing 250 ms fade.

A slow-encoder regression test holds encoding behind a barrier while the real
async pipeline processes further frames and publishes snapshots. Independent
keyframe deadlines now hit 0, 1.5 and 3 seconds in a stable scene instead of
rounding up to snapshot heartbeats. Local/remote object tests verify scans at
capture deadlines regardless of how many frames complete between them.

Reconnect tests enqueue 64 superseded snapshots and two keyframes. Only the
newest snapshot, the ordered event and the newest intact header/binary pair
reach the socket. Additional cases cover state expiry and replacement while a
send is in flight. A repeated socket probe measured 656 ms before and 10.5 ms after, with
63 stale states dropped instead of replayed.

Both local model predictors warm before live processing. A failed GPU handshake
starts on the warm fallback without immediately attempting another handshake.
The audit's isolated CPU thread sweep favored four threads over eight
(73 vs 116 ms median for the same two-model call); the CLI exposes this budget
because venue workload and hardware still determine the best value. Warmup
uses a synthetic 720p frame and never seeds audience/object facts.

Voice fetch/decode now overlaps back-layer loading and the fade. Consumers
share one request per URL, cancellation is independent, and the final consumer
aborts abandoned work. Late decoding cannot cache or play a retired voice.
Approved copy/voice cache warmup already exists as `make demo-warmup`; use it
before rehearsal rather than adding provider spending to the hot loop.

## Verification and reproduction

```sh
make check
make edge-check
make web-check
make test-sandbox
make eval
cd web && npm run build && cd ..
uv run --with playwright python scripts/bench_sensing.py --detector-ms 80
```

`--edge-root /path/to/checkout/edge` compares the same probe against a baseline
checkout. Set `--edge-python` when using a shared Python 3.12 environment.
Results go to ignored `evals/results/`. No frames or audio are recorded.

Replay shifts every recorded device timestamp together, preserving sensing
durations instead of mixing historical candidates with a fresh measured_at.
Offline clip recordings omit wall-clock detection/publication timestamps.

Regression coverage includes trace propagation to the player, tracker holds,
passer-by rejection, early positive safety blocking with conservative clearing,
keyframe scheduling/non-blocking delivery, reconnect state, inference warmup,
object cadence, and voice prefetch/cancellation. The existing product scenarios
and soak retain their KPI ratchets; no baseline is lowered.

Candidate, detected and published timestamps share the device clock. Candidate
time includes observed confirmation/holding, while `measured_at` remains the
latest contributing frame. Legacy traces fall back to measured time. Sync
clocks before interpreting device/backend/player differences. The cache-hit
budget starts after sensing confirmation. Camera exposure, driver buffering,
remote GPU RTT, live providers and physical TV latency still need a venue
rehearsal before claiming glass-to-glass production SLO compliance.

## Second loop: outbound backpressure and shared voice admission

The next audit tested four hypotheses: a global console lock couples clients,
player writes overlap during backpressure, shared voice waiters exhaust provider
admission slots, and QR mask selection consumes significant decision time.

Controlled probes against `a7149c4`, followed by the same probes after the fix
(10 trials, 120 ms per slow-sink send and a 200 ms held voice synthesis):

| Measurement (median) | Before | After |
|---|---:|---:|
| Healthy console: complete decision batch | 240.75 ms | 0.12 ms |
| Healthy console: next decision | 361.09 ms | 0.16 ms |
| Concurrent writes on one player connection | 2 | 1 |
| Different voice offer: provider admission wait | 200.64 ms | 0.07 ms |
| Actual voice calls / results delivered | 2 / 5 | 2 / 5 |

These probes isolate queue behavior; they are not network/physical display
percentiles. QR encoding measured 1.95 ms median / 2.11 ms p95 over 50 synthetic
redemption URLs and remains a lower-priority CPU cost. No QR quality tradeoff
was introduced.

Outbound delivery now preserves each decision batch's order per connection.
Healthy connections proceed independently of slow ones. Each connection permits
at most 32 active/pending batches; overflow or a send timeout retires pending
writes and closes the actual WebSocket with code 1013. The existing player
reconnects and receives the current creative while retaining its last good ad.
Normal disconnects cancel active writes. No new transport or hot model call
is introduced; ready-to-displayed traces continue to include outbound delivery.

A warm voice job that finds its synthesis already running subscribes to that
result and releases its provider slot. Each screen gets its own notification,
with the existing current-line/current-voice guard. Repeated subscriptions from
the same screen replace its callback. Failure notification and subscriber
exceptions are isolated, and a shared synthesis still spends once. Unique
syntheses continue to use the configured provider concurrency limit.

Verification includes failing-then-passing backpressure regressions, real
agent/player/console WebSockets with a blocked console, actual 1013 close and
reconnect recovery, three screens sharing two voice calls, stale subscriber
suppression, callback isolation and provider quota failure. Full backend and
edge suites, 78 web tests/build, sandbox speech/TTS and product evals pass.
The after-run server-decision p95 was 4.27 ms, with no invariant violations.
The pipeline eval passed with warmed complete-ad p95 8.70 ms and generated
complete-ad p95 1,042 ms; those measure socket availability with recorded copy
and sandbox TTS timing, not physical audio/visual completion.

Three separate real edge/backend/Chromium trials with simulated detection
service times of 40/80/140 ms measured 922/996/1,189 ms scene-to-visible, with
zero uncaught browser errors. Live camera buffering, GPU RTT, paid provider
behavior and TV latency still require a venue rehearsal.

Reproduce the controlled comparison with:

```sh
uv run python scripts/bench_outbound.py --out evals/results/outbound.json
# Use --source-root /path/to/baseline/checkout with the same probe for comparison.
make eval-pipeline ARGS="--cold --dwell 2"
```

No product KPI ratchet changed, so the existing baseline remains intact.
