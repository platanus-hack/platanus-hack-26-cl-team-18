# Delivery

Playback records which creative actually appeared on a screen and when.
Delivery joins the reported playback window with the screen's looking samples
to measure attention-seconds, then applies the clearing prices recorded for
the creative's own auction. Charges are idempotent, append-only ledger entries.

- **Attention** (`domain/attention.py`): samples are the agent's anonymous,
  session-local tracklet ids, held in memory for 10 minutes. Each is held until
  the next snapshot, for at most 10 s. A look counts only if it lasts at least
  1 s within the play window (D7), and then counts in full. Shorter glances
  bill nothing.
- **Charges** (`domain/charge.py`): every brand in a basket pays its own
  `price_per_as × attention_seconds`, because the slot share is already in the
  price. Each charge is rounded once, half-even, to `Money`. Zero attention
  records an explicit $0 charge with reason `no_attention`.
- **Billing** (`application/billing.py`): idempotent by `screen:creative:seq`.
  The same key with a different window raises `ConflictingPlaybackReport`.
  House ads and unknown creatives are never billed. A receipt feeds
  `console.receipt`, and its charges settle the campaigns' budget holds.
- **Ledger** (`infrastructure/ledger.py`): kept in memory, and written behind
  to the Postgres `charge` table when `DATABASE_URL` is set.
