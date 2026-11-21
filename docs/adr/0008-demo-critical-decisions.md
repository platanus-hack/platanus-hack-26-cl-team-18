# 0008: Demo-critical decisions from the 2026-11-21 audit

- Status: accepted
- Date: 2026-11-21

## Context

An audit of the docs against the scaffold (commit `e880589` plus work in
progress) found that the architecture holds, and that the risk sits in build
order, recognition reliability, phone reachability, pricing claims and demo
logistics. Each decision below names what changes and who it affects.

## Decisions

### D1. Build order: the vision path first

Every beat depends on vision; speech is a backup moment. Order:

1. Local vision path end to end: `edge/agent/vision` → `audience.snapshot` →
   moment → auction → player. **Implemented (edge side):** latest-frame
   capture, YOLO26 pose + objects adapter, IoU tracker with anonymous ids,
   looking/distance from keypoints, held products from markers, snapshot
   rate-limiting, face-blurred keyframes, replay from a video file, and a
   `FailoverDetector` ready for the remote GPU (`edge/tests/test_vision.py`).
2. Held products wired into the auction (D2 hand-off).
3. Remote GPU service (`gpu/`), only after the local path works; it plugs in
   as the failover primary without other changes (ADR 0007).
4. Speech mode last, as a backup moment.

### D2. Props carry ArUco markers; the label-reading VLM runs in parallel

"Complete the basket" (beat 3) must not depend on a vision model reading a
cup label under hall lighting. Each prop carries a printed **ArUco marker
(DICT_4X4_50)**. Detection takes about 7 ms in the hot path (measured on a
synthetic 720p frame), and a held product is debounced over 8 frames (3
hits) and counts only near a person box. `edge/props/markers.json` maps
marker id → **brand slug**.

- **Wire convention until the contract has a field:** a held product travels
  as `DetectedObject(label="product:<brand-slug>", score=confidence)`.
- **Hand-off (contract owner):** add `held_products: list[HeldProductDTO]`
  to `AudienceSnapshot` in `app/contract/messages.py`, run `make contract`,
  and switch `agent/vision/snapshot.py` over.
- **Hand-off (audience/bootstrap owner):** map `product:<slug>` → `BrandId`
  and pass `HeldProduct`s into `AdOpportunity.held_products` (the valuation
  already reads it). The demo seed mints random UUIDv7 brand ids, so it needs
  a stable **slug per brand kit** (`voltio`, `cafe-patio`, `rayo`, …).
- **Honesty:** if a judge asks, say the props carry markers for reliability
  and production uses product recognition (the VLM path, shown in parallel
  on the console).

### D3. Judges' phones reach `/advertiser` and `/r/{code}` through a tunnel

Phone hotspots and venue Wi-Fi often isolate clients, so a judge's phone may
not reach the laptop. Expose **only** those two routes through a public
tunnel (`cloudflared tunnel --url http://127.0.0.1:8000`, or the deployed
Render URL as a relay). Judges then use mobile data. The screen loop stays on
localhost. Show the tunnel URL as a QR on the console.

### D4. At most 2 brands per basket for the hackathon

The code clears `solo` and `duo` only; two logos are also easier to read.
Trio *layouts* are later. "Trío" remains the **offer** for 3 people, which is
unrelated to the number of brands.

### D5. Coverage is the live headline; revenue lift is a session total

`auction-plan.md` R-P3: with core-adjusted prices, basket **revenue** is not
guaranteed to exceed the single-brand counterfactual, and the simulator's
"+20 %" is auction **value**, not revenue. Live, during the bidding war, a
basket can honestly earn less than solo. So:

This follows `auction-plan.md` D6 (headline = coverage + value lift; revenue
lift shown only when positive; in-category rivals seeded so the seeded lift is
real) and adds the pitch side:

- The live headline the narrator points at is **audience coverage**.
- Revenue vs. the static screen is spoken about as a **session total** (money
  jars), never as a per-beat "+X %", because the bidding war is unscripted.
- Seeded scenes keep their test that asserts the lift we pitch.
- "Value" and "revenue" are used exactly as the auction plan defines them.

### D6. CLP and Chilean Spanish everywhere

Money is CLP (0 decimals): offers like "$3.490", moment charges like "$114".
Screen copy and voice-overs are Chilean Spanish with a Latin-American voice.
The pitch is in Spanish unless the team decides otherwise.

### D7. Two laptops on demo day

The screen laptop runs the agent, the backend and the player. The **console
runs on a second laptop** connected to the backend over the network, so the
projector and console rendering can't make the screen laptop overheat and
slow down.

### D8. Minimum display time: already implemented

`app/modules/auction/domain/swap.py` holds a new group for 1.5 s and a
same-moment re-auction (bid, stock, conditions, held products) for 3 s, with
2 % hysteresis. The bidding war uses it as is. The console shows the
challenger as "pending" until the swap.

### D9. One brand catalog

The seed in `app/bootstrap/demo.py` is the source of truth for brand names,
offers and partner pairs (Voltio, Café Patio, Rayo, …). Docs reference it
instead of keeping their own list.

### D10. Perception noise is filtered on the agent (second audit)

The backend has no moment debounce of its own (only the screen-swap hold in
`swap.py`), so raw YOLO flicker would trigger an auction on every snapshot.
The agent filters, in this order, matching the stand-in already validated in
`evals/video/perceive.py`:

1. **Near-screen filter:** a person counts only if their box is at least
   18 % of the frame height. Far passers-by in a busy hall are not the
   audience.
2. **Track confirmation:** a track counts after 2 detections, so a one-frame
   false positive never becomes a person.
3. **Objects near people only:** an object counts only if it overlaps a
   counted person (padded 15 %). A bottle on the counter is not held.
4. **Fact debounce (500 ms):** people, looking, objects and held products are
   published only once they have held for 500 ms. Counts in a snapshot are
   these published facts; tracklets stay live for attention measurement.
5. **No freezing in a crowd:** if the facts never hold still (a busy counter),
   the most frequent facts of the last second are published every 1.5 s. Found
   on a real café clip, where the count otherwise froze at 3 for 9 s while
   5–8 people came and went.

Verified on real video: YOLO26 (n-pose + s objects, CPU) on the
`coffee-counter` stock clip from `evals/video/clips.json`. About 115 ms per
frame on CPU, a stable count that follows the crowd (3 → 5 → 7 → 5 → 4),
and cup/laptop detected near people. The recording loads in
`evals/perception.py`.

### D11. The agent records the eval replay contract

`python -m agent.vision --video clip.mp4 --record out.jsonl --offline` reads
the clip on clip time (sampled, not paced, no wall clock) and writes
`{"t_s", "snapshot"}` lines (docs/evals.md §4), plus a `.meta.json` with
inference p50/max. `make eval-perception` and `make eval-replay` now score the
real agent. **Hand-off (evals owner):** `evals/video/perceive.py` was a
stand-in "until edge/agent/vision lands"; it can be retired once the golden
clips have been re-recorded with the agent.

### D12. One canonical vision path; `on-sight` stays a debug tool

The merge of `0a69119` brought a second, standalone vision slice
(`edge/agent/vision/replay.py` + `state.py`, the `on-sight` script). Decision:

- **Canonical live path: `python -m agent.vision`.** It is the only one that
  speaks the contract (`audience.snapshot`, `audience.keyframe`), uses pose for
  *looking*, reads prop markers, debounces, fails over (ADR 0007) and records
  the eval replay contract (D11). The backend only consumes this path.
- **`on-sight` (`replay.py`/`state.py`) stays as a local debug tool.** It
  uses a detection-only model and doesn't feed the auction. Don't build demo
  features on it. Since PR #1 it can publish a separate, ephemeral
  `recognition.snapshot` feed (ByteTrack tracks, direction, departed people,
  late VLM attributes on head-redacted crops) into an in-memory
  `RecognitionStore` (64 screens, 30 s TTL). Nothing subscribes to it in the
  loop; `GET /recognition/{screen}` and the VLM (`VLM_ENABLED`) are off in
  prod. It is the workbench where the ideas below are proven before they
  move into `agent.vision`.
- **Port these ideas from it into the canonical path, by demo value:**
  1. **Movement direction** (left/right/stationary per track), so a passer-by
     who walks through doesn't count like someone who stops.
  2. **`enrich()`**: merge a late warm-path (VLM) result only into the track it
     was observed on, keeping the stronger evidence. That is how VLM tags and
     `minor_likely` should attach to tracks.
  3. **ByteTrack** behind the tracker interface if the IoU tracker shows ID
     switches in crowds (measure with `make eval-perception` first).

### D13. A pose heuristic supplements the VLM path for `minor_likely`; it only blocks

`edge/agent/vision/minor.py` flags a tracked person as child-sized from two
pose cues: stature against peers on the same floor line, and ear-to-shoulder
width. A 7-of-12-frame vote then feeds the 500 ms fact debounce. Only the
moment's boolean and a confidence capped at 0.9 leave the device. No
per-person data is kept or logged, and there is no face analysis or age
estimate. Invariant 2 still holds: the flag can only block age-restricted ads.
Thresholds live in `MinorConfig` and can be set with `--minor-threshold`,
`--minor-window` and `--minor-hits`. In the same change, `edge/agent/sound/`
adds an always-on sound scene built from coarse acoustic features (no words,
no audio stored or sent), enabled with `--sound`. Its "coffee service
fixture" label is a demo heuristic.

### D13 amendment. Publish a confirmed minor block immediately

The temporal pose vote still decides when the minor flag becomes true. Once
confirmed, the edge publishes that block without waiting for the general
scene debounce or its rate limit. Removing a block still requires a stable
scene, and the crowd vote cannot clear it. At 10 fps the fixture reaches the
backend boundary 600 ms after the first hit, 500 ms earlier than before.

## Consequences

- Two hand-offs (D2) are needed before beat 3 works end to end.
- The pitch wording changes (D5); `docs/demo.md` is updated.
- Logistics (D3, D7) go on the demo-day checklist.


## Amendment: live startup and `on-sight` backend mode

The live market now registers in every environment independently of
`DEMO_SIMULATOR`, with `LIVE_ADS_ENABLED=false` as an explicit transport-only
escape hatch. Each wire screen id gets its own audience/auction/creative state;
campaigns, budgets, charges and redemption codes are shared across the market.
The seed remains the approved brand catalog. Operator mutation endpoints stay
disabled in production.

D12 is amended for `on-sight --backend`: this mode now delegates to the
canonical pose pipeline and drives ads without inventing attention or safety
facts from detection-only boxes. `--recognition-only` preserves the existing
workbench contract and crop enrichment. The debug recognition feed itself
continues to carry observations rather than fabricated auction facts.

## Amendment: purpose-specific sensing filters and freshness

Profiling found that ID retention and looking smoothing were followed by a
second 500 ms snapshot debounce. Keep 500 ms debounce for arrival, objects,
held products and sound. Separate the 800 ms association lifetime from a
300 ms presence hold. Looking retains its 500 ms tracker hold and publishes
without another fact debounce when group size is unchanged. The minor vote
still requires seven hits out of twelve: publish a positive vote immediately
subject to the 10 Hz rate limit, and retain the ordinary debounce on clearing.
A crowd vote cannot clear an active safety block early. This amends D10/D13.

The canonical agent prewarms both local model predictors and the remote
connection before live inference. Objects use a 200 ms capture-time deadline;
face-blurred keyframes use an independent 1.5 s deadline with one active encode
in a worker and no raw-frame backlog. Pending reconnect state is conflated per
screen/type and expires after 1.5 s of queue residence. Events remain ordered
and keyframe header/binary pairs remain indivisible.

Traces include candidate, detected and published boundaries, while measured_at
continues to identify the latest contributing frame. Candidate time includes
observed tracker confirmation and holds; camera exposure, device buffering and
clock synchronization still require a hardware rehearsal. The <300 ms cache
budget applies after sensing confirmation; scene-to-visible retains the
1.5 s p50 / 2.5 s p95 targets. No model result gates first visual readiness.
