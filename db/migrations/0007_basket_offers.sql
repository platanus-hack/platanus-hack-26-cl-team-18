-- An issued QR remains valid across a backend restart. Only approved offer facts,
-- never sensor input, are stored. Charges/redemptions remain their existing ledgers.
CREATE TABLE basket_offer (
    code text PRIMARY KEY,
    organization_id uuid,
    issued_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    payload jsonb NOT NULL
);
CREATE INDEX basket_offer_organization_issued_idx ON basket_offer (organization_id, issued_at DESC);
CREATE TRIGGER basket_offer_append_only BEFORE UPDATE OR DELETE ON basket_offer
    FOR EACH ROW EXECUTE FUNCTION reject_history_rewrite();
CREATE TRIGGER basket_offer_no_truncate BEFORE TRUNCATE ON basket_offer
    FOR EACH STATEMENT EXECUTE FUNCTION reject_history_rewrite();

CREATE TABLE playback_receipt (
    dedupe_key text PRIMARY KEY,
    organization_id uuid,
    screen_id text NOT NULL,
    recorded_at timestamptz NOT NULL,
    payload jsonb NOT NULL
);
CREATE INDEX playback_receipt_organization_screen_idx
    ON playback_receipt (organization_id, screen_id, recorded_at DESC);
CREATE TRIGGER playback_receipt_append_only BEFORE UPDATE OR DELETE ON playback_receipt
    FOR EACH ROW EXECUTE FUNCTION reject_history_rewrite();
CREATE TRIGGER playback_receipt_no_truncate BEFORE TRUNCATE ON playback_receipt
    FOR EACH STATEMENT EXECUTE FUNCTION reject_history_rewrite();
