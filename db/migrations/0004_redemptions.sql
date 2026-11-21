-- Basket redemptions (architecture §6.2): credited to every brand in the basket.
-- Append-only. The device nonce is stored only as a SHA-256 hash, and a code is
-- redeemed at most once per device.
CREATE TABLE redemption (
    redemption_id uuid PRIMARY KEY,
    code text NOT NULL,
    auction_id uuid NOT NULL,
    screen_id text NOT NULL,
    campaign_ids jsonb NOT NULL,
    nonce_hash char(64) NOT NULL,
    redeemed_at timestamp with time zone NOT NULL,
    recorded_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT redemption_once_per_device UNIQUE (code, nonce_hash)
);

CREATE INDEX redemption_auction_idx ON redemption (auction_id);
