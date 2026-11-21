# Run the audience replay

The simulator exercises the same agent and player WebSockets as the device
pipeline. It uses one local screen, two fixture classification catalogs, a
seeded campaign book, browser player, and operator console. It needs no camera,
microphone, Postgres, or provider credentials.

## Start the backend and player

Run each command from the repository root in a separate terminal.

```sh
make install
make dev
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
| `uv run python -m scripts.replay_audience` | Voltio + Café Patio offer, 100% viewer coverage, basket value 8.6 vs solo 8 and revenue 6.95 vs 6.5 CLP/s, and Rayo excluded as a competitor |
| `uv run python -m scripts.replay_audience --scene fitness` | Voltio solo |
| `uv run python -m scripts.replay_audience --scene adult` | Malta Sur solo |
| `uv run python -m scripts.replay_audience --scene minor-safe` | House ad, with Malta Sur blocked by minor safety |
| `uv run python -m scripts.replay_audience --scene empty` | House ad with zero looking viewers |

The script verifies the creative over a player socket and prints the result.
When the expected outcome already plays, it sends a different fixture scene
first so the requested replay produces an observable transition. Use
`--server ws://127.0.0.1:8000` to point the script at a different local port.
The live pipeline is enabled by default, including in production. The
`DEMO_SIMULATOR` setting no longer gates sensing, auctions or playback.
Use loopback for synthetic observations. Operator mutations stay disabled
in production; QR redemption and the live loop remain available.

## Follow a snapshot through the code

1. [Replay script](../scripts/replay_audience.py) sends an `audience.snapshot` to
   `/ws/agent` with structured observations and the measurement instant.
2. [Live market wiring](../app/bootstrap/live.py) resolves each wire screen id to its own runtime and typed screen
   ID, rejects stale measurements, and suppresses repeated audience facts.
   The trace preserves device time. Ordering caps it at server receipt so a
   fast device clock cannot delay a subsequent safety update.
   The [audience classifier](../app/modules/audience/application/classifier.py)
   maps observations through the [IAB catalog](../app/modules/audience/infrastructure/catalog/iab_audience_1_1_cstore.json)
   into real IAB Audience Taxonomy 1.1 segments (Fitness `408`, Coffee `977`,
   Alcoholic Beverages `369`), fusing vision, sound and (with
   `ANTHROPIC_API_KEY`) the VLM's keyframe tags; speech topics merge in after.
   The replay labels (`sports ball`, `tap handle fixture`, `coffee service
   fixture`) are demo-only cues at full weight. A `product:<slug>` object is a
   prop marker: it becomes a held product, never a segment.
3. The [auction desk](../app/modules/auction/application/desk.py) passes an
   `AdOpportunity` (the moment, `Bidder`s, layouts, reserve, approved baskets)
   to the [engine](../app/modules/auction/domain/clearing.py). The engine
   records a reason for every losing bidder (safety, competitor, not a
   partner, below reserve, …). It selects the best basket by slot efficiency
   and prices it with core-adjusted VCG, per attention-second as `MoneyRate`.
   It also computes the single-brand counterfactual (value and revenue). The
   desk then applies minimum ad seconds: outside `app_env=test`, a new group
   swaps after 1.5 s at the earliest, and safety or a stock-out swaps at once.
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

The approved seed catalog is shared across screen runtimes. Audience moments,
stock, swap holds and current creatives belong to each screen; campaign identities,
budgets, delivery and redemptions belong to the market. For example, open
`/screen?id=north` and `/screen?id=south`, and send observations with the matching
`screen_id`. A reconnect receives that screen's last creative. `/clerk?id=north`
reads and writes north's stock, while budgets remain shared.

Run `make live-preflight` to inspect configuration without spending provider
credits. `/health` exposes the same `ads` section. `configured` means an adapter
was built, not that a live provider request succeeded. `make live-preflight
ARGS="--require-providers"` fails if any provider is disabled/unconfigured or
any of the six expected seed loops is missing. Approved stock copy, a silent
creative and a template remain available when optional providers are absent.

AI copy and audience tagging default on, but need `ANTHROPIC_API_KEY`.
Existing `.env` values of `AUDIENCE_AI=false` or `CREATIVE_AI_COPY=false` still
explicitly disable them. Voice needs both `ELEVENLABS_API_KEY` and
`ELEVENLABS_NARRATOR_VOICE_ID`; `VOICEOVER_PREWARM=true` fills approved basket
voice lines at startup within the persistent character budget.

Cold video uses `FAL_KEY` and a verified `FAL_VIDEO_MODEL`. Install ffmpeg,
then run `make bundle-loops ARGS="--budget-usd <total> --cost-per-clip-usd <maximum>"`
with the provider model's verified maximum request charge. It generates only
approved pairs and solos, transcodes to H.264 720p, and skips cached files.
Each submitted job leaves an `.attempt.json` intent. If generation times out or
fails, the command refuses another submission for that job until an operator
reconciles it with the provider and either installs the recovered MP4 or removes
the intent after confirming no request can still complete. It never retries a
potentially billed request automatically. No generation runs in the hot path.

## Drive the market: bidding war, stock, billing

With the backend and `npm run dev` running, open these pages:

- [/advertiser?brand=Rayo](http://127.0.0.1:5173/advertiser?brand=Rayo): one
  brand's bid slider (×0.2–5.0). Leave out `brand` to get a picker.
- [/clerk?id=demo-01](http://127.0.0.1:5173/clerk?id=demo-01): stock toggles.

Each change re-auctions the moment on screen:
- A bid flip waits for the current teaser (3 s) and needs a 2 % margin.
- A stock-out of a brand on screen swaps at once.

The console's **Market** panel shows bids, stock, spend and holds. Its
**Moment receipt** panel shows each billed play, for example
"2 looked · 4.000 AS · Voltio $9". When a play gets no qualifying looks, it
shows "Charged $0: nobody looked" instead.

The replay scenes carry anonymous tracklets, so plays are billed. Set
`DATABASE_URL` (after `make db-up`) to also record auctions and charges in
Postgres.
