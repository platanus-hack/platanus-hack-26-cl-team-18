# Run the audience replay

The simulator exercises the same agent and player WebSockets as the device
pipeline. It uses one local screen, two fixture classification catalogs, a
seeded campaign book, browser player, and operator console. It needs no camera,
microphone, Postgres, or provider credentials.

## Start the backend and player

Run each command from the repository root in a separate terminal.

```sh
make install
DEMO_SIMULATOR=true make dev
```

```sh
cd web
npm install
BACKEND_URL=http://127.0.0.1:8000 npm run dev
```

Open the [demo screen](http://127.0.0.1:5173/screen?id=demo-01) and
[operator console](http://127.0.0.1:5173/console), then replay these scenes
from the repository root.

| Command | Player and console |
|---|---|
| `uv run python -m scripts.replay_audience` | Voltio + Café Patio offer, 100% segment coverage, 20% more auction value than solo, and Rayo excluded as a competitor |
| `uv run python -m scripts.replay_audience --scene fitness` | Voltio solo |
| `uv run python -m scripts.replay_audience --scene adult` | Malta Sur solo |
| `uv run python -m scripts.replay_audience --scene minor-safe` | House ad, with Malta Sur blocked by minor safety |
| `uv run python -m scripts.replay_audience --scene empty` | House ad with zero looking viewers |

The script verifies the creative over a player socket and prints the result.
When the expected outcome already plays, it sends a different fixture scene
first so the requested replay produces an observable transition. Use
`--server ws://127.0.0.1:8000` to point the script at a different local port.
The backend enables this wiring only when `DEMO_SIMULATOR=true` outside
production. Run it on loopback for these synthetic observations.

## Follow a snapshot through the code

1. [Replay script](../scripts/replay_audience.py) sends an `audience.snapshot` to
   `/ws/agent` with structured observations and the measurement instant.
2. [Demo wiring](../app/bootstrap/demo.py) resolves `demo-01` to a typed screen
   ID, rejects stale measurements, and suppresses repeated audience facts.
   The trace preserves device time. Ordering caps it at server receipt so a
   fast device clock cannot delay a subsequent safety update.
   [Fixture tagging](../app/modules/audience/application/fixture_tagging.py)
   maps structured video and sound observations into one estimate per matched
   segment. Fixture IDs use a `DEMO_` prefix.
3. [Auction](../app/modules/auction/domain/bundle.py) receives an
   `AdOpportunity` with the `AudienceMomentSnapshot`, campaign bids and
   supported layouts. It filters age-restricted bids, checks category
   compatibility and mutual opt-in, then selects a bundle and computes its
   VCG-style prices and best solo counterfactual using Decimal and Money.
4. [Creative composition](../app/modules/creative/application/template.py)
   builds a template from stock copy and fixture brand kits. The demo reuses
   the repository's project mark across the fictional brands. The player
   receives `creative.ready`, preloads the image and crossfades to the template.
   A no-fill result sends a house ad. A reconnect receives the current creative
   on its own socket.
5. [Decision projection](../app/bootstrap/demo_console.py) sends the console
   the classified cues, relevant bids, winners, exclusions and latency trace.
   A reopened console receives the last decision. Server decision time runs
   from receipt to creative readiness. Glass-to-glass needs a display
   timestamp from the player.

The seeded campaigns use one bid each and the screen offers solo and duo
layouts. The duo splits screen share evenly and receives an affinity bonus
for the two mutually opted-in brands. With the co-brand fixture the duo is
worth CLP 120 versus CLP 100 for the best solo. Voltio clears at CLP 40
per attention-second because Rayo could take its place with Café Patio.
Café Patio clears at CLP 30. The shopper-facing CLP 3,490 offer is fixture
copy. Segment coverage counts matched segments in this replay.

The [context map](../CONTEXT-MAP.md) covers the full design. The replay stops at
template playback and the console. Live recognition, IAB catalog resolution,
time-based debouncing, warm media upgrades, QR redemption and billing remain
integration work. The simulator stores its current state in memory and
uses the existing WebSocket schema.
