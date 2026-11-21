# Demo video: "Three friends at the counter"

The recorded demo (about 2:30) follows the live script in [demo.md §3](demo.md#3-the-3-minute-script-five-beats-no-more)
beat for beat. Every shot is proven by `make eval-demo-video`, which runs the same
beats through the live backend and fails if any ad is incomplete. It spends no
provider credits. Run it before every recording session.

## Setup

- **Place:** a convenience-store counter (or a table dressed as one). The TV sits
  above it in Game mode, the webcam is under the TV, and the speakerphone is on
  the counter.
- **Props:** a gym bottle, two coffee cups, the printed
  [Café Patio marker](../edge/props/cafe-patio-marker.svg) taped to one cup, and
  a phone for the QR.
- **Cast:** three people (A, B, C) and one presenter on the `/demo/<key>/advertiser`
  laptop.
- **Cameras:** cam 1 is wide and shows the counter, people and TV. Cam 2 is a
  screen capture of `/console?screen=demo-01`. Cut between them; the console is
  the proof.

## Shot list

| # | t | Shot | On the TV | On the console | Proven by (`eval-demo-video`) |
|---|---|---|---|---|---|
| 1 | 0:00 | Static insurance ad on a normal store screen. VO: *"Store screens are still blind."* | (house ad) | — | `0. never blank` |
| 2 | 0:15 | A and B step up with the bottle and cups and look at the TV. | Voltio + Café Patio · **Dúo post-entreno $5.990**, voice-over, energy-coffee loop | Coverage 100 %, basket beats the single-brand counterfactual | `2. Basket ad · Dúo` (11 checks) |
| 3 | 0:35 | C joins. | *"Pausa corta. Café para los tres."* Same brands, **Trío post-entreno $8.490** | People 3, new price, latency trace | `2. Basket ad · Trío` |
| 4 | 0:55 | B turns the Café Patio cup marker toward the camera. | *"A tu Café Patio le falta Voltio."* · Trío $8.490 | Held product chip: Café Patio | `3. Complete the basket` |
| 5 | 1:20 | The presenter drags Rayo's slider to 1.5×. | After the teaser finishes, **Rayo + Café Patio · Trío $7.990**, *"A tu Café Patio le falta Rayo."* | Voltio excluded: **competitor**; Café Patio keeps its seat | `4. Bidding war` |
| 6 | 1:45 | They keep watching; the ad reaches its close. C scans the QR and taps **Canjear en caja**. | QR + code + "válido 10 min" | Redemption credited to **both** brands | `5. Close · redeem · receipt` |
| 7 | 2:05 | They walk away. | House ad | Receipt: Rayo and Café Patio billed for attention-seconds | `receipt bills both brands` |
| 8 | 2:15 | A glances back without looking at the screen, then leaves. | Basket, then the house ad | **Charged $0** | `5. Charged $0` |
| 9 | 2:25 | Closing line over the console: *"We sell moments, not slots."* | — | — | — |

What changes on screen, and why:

| Input | Changes | Shot |
|---|---|---|
| Camera: group size | Price tier (Dúo → Trío) | 3 |
| Camera: held product (marker) | The pitch (complete the basket) | 4 |
| Camera: looking / dwell | Teaser → offer → close; $0 when nobody looks | 6, 8 |
| Camera: distance | Far: big headline and price; near: big QR | 6 (step closer to scan) |
| Market: bid slider | Which brand shares the basket | 5 |
| Voice (backup) | "café" completes the basket; "cerveza" near a minor is blocked | backup take |

## Recording checklist

1. `make eval-demo-video` passes (60/60 checks, 7/7 beats), then `make eval` and `make eval-speech`.
2. Warm the caches on the demo backend (`make demo-warmup`) so the AI copy and
   voice-overs are cached. A recording should not wait on providers.
3. Build each basket's own video loop once: `make bundle-loops-local`. It is
   free: it composes approved campaign footage graded to each brand's accent
   (`cafe-patio+voltio.mp4`, `cafe-patio+rayo.mp4`, …). `make bundle-loops`
   (fal, paid) can replace them later; delete a composed file to let it.
4. Rehearse with the real webcam and record it:
   `make eval-record OUT=evals/results/rehearsals/take-1.jsonl`. Then
   `make eval-beats REC=…` shows which beats landed in order, and
   `make eval-rehearsal REC=…` checks latency and invariants. Keep the best
   take: it is the real-camera regression.
5. Backup takes: stock-out via `/demo/<key>/clerk` (`stock-out` scenario) and speech mode
   (`speech-camera-mix`), in case a beat fails on camera.

## Known limits (say them if asked)

- Bundle loops are composed from approved campaign footage, not newly
  generated per bundle. Voltio and Rayo share the energy shot, graded to
  different colours.
- Copy, voice-over and video timing in the eval come from replayed Claude
  latency and the sandbox ElevenLabs server. The live providers run only in
  the warm-up and on the day.
