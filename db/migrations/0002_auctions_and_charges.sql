-- The record of every auction and every charge (docs/auction-plan.md R-O6, delivery ledger).
-- Both are append-only history: rows are inserted once and never updated or deleted.
-- The hot path never reads these; it writes them behind (WriteBehindWriter).

CREATE TABLE auction (
    auction_id uuid PRIMARY KEY,
    screen_id text NOT NULL,
    moment_id uuid NOT NULL,
    organization_id uuid,
    trigger text NOT NULL,
    pricing text NOT NULL,
    layout text,
    no_fill text,
    currency char(3) NOT NULL,
    bundle_value numeric(22, 6) NOT NULL,
    bundle_revenue numeric(22, 6) NOT NULL,
    single_brand_value numeric(22, 6) NOT NULL,
    single_brand_revenue numeric(22, 6) NOT NULL,
    coverage numeric(7, 6) NOT NULL,
    people integer NOT NULL,
    looking integer NOT NULL,
    minor_likely boolean NOT NULL,
    winners jsonb NOT NULL,
    bids jsonb NOT NULL,
    exclusions jsonb NOT NULL,
    moment_changed_at timestamp with time zone NOT NULL,
    cleared_at timestamp with time zone NOT NULL,
    recorded_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE INDEX auction_screen_cleared_idx ON auction (screen_id, cleared_at DESC);

CREATE TABLE charge (
    charge_id uuid PRIMARY KEY,
    dedupe_key text NOT NULL,
    organization_id uuid,
    auction_id uuid NOT NULL,
    creative_id text NOT NULL,
    screen_id text NOT NULL,
    campaign_id uuid NOT NULL,
    brand_id uuid NOT NULL,
    attention_seconds numeric(12, 3) NOT NULL CHECK (attention_seconds >= 0),
    price_per_as numeric(22, 6) NOT NULL CHECK (price_per_as >= 0),
    amount numeric(22, 6) NOT NULL CHECK (amount >= 0),
    currency char(3) NOT NULL,
    reason text NOT NULL CHECK (reason IN ('attention', 'no_attention')),
    played_from timestamp with time zone NOT NULL,
    played_to timestamp with time zone NOT NULL,
    recorded_at timestamp with time zone NOT NULL,
    -- One charge per brand per billed playback report: retries never double-bill.
    CONSTRAINT charge_dedupe_unique UNIQUE (dedupe_key, campaign_id)
);

CREATE INDEX charge_campaign_idx ON charge (campaign_id, recorded_at DESC);
CREATE INDEX charge_auction_idx ON charge (auction_id);
