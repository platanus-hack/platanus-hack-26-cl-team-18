-- The campaign record (app/modules/campaigns/application/ports/registry.py).
-- The catalog (brands, rates, partners, offers) stays in code; this table keeps what
-- must outlive a deploy: one stable campaign and brand id per brand slug, the budget,
-- and the last live bid multiplier. Spend is never stored here: it is derived from
-- the append-only charge ledger at startup.
CREATE TABLE campaign (
    campaign_id uuid PRIMARY KEY,
    brand_id uuid NOT NULL UNIQUE,
    brand_slug text NOT NULL UNIQUE,
    organization_id uuid,
    currency char(3) NOT NULL,
    budget numeric(22, 6) NOT NULL CHECK (budget >= 0),
    live_multiplier numeric(8, 4) NOT NULL DEFAULT 1 CHECK (live_multiplier > 0),
    -- The bidding war's ms-clock seq: a write only lands when it is newer.
    multiplier_seq bigint NOT NULL DEFAULT 0,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL
);
