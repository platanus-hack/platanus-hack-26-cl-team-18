-- Tenant indexes and database-level append-only enforcement.
-- Idempotent: safe to re-run by hand against a database that already has it.

-- Every query on advertiser/venue data filters by organization_id (CLAUDE.md, multi-tenant).
CREATE INDEX IF NOT EXISTS auction_organization_cleared_idx
    ON auction (organization_id, cleared_at DESC);
CREATE INDEX IF NOT EXISTS charge_organization_recorded_idx
    ON charge (organization_id, recorded_at DESC);
CREATE INDEX IF NOT EXISTS campaign_organization_idx
    ON campaign (organization_id);

-- History is inserted once (ON CONFLICT DO NOTHING on the natural key) and never
-- rewritten. The code-level guardrail (tests/unit/architecture/
-- test_domain_pattern_guardrails.py, APPEND_ONLY_TABLES / IMMUTABLE_ROW_TABLES) now
-- has a database backstop: a stray UPDATE, DELETE or TRUNCATE raises.
CREATE OR REPLACE FUNCTION reject_history_rewrite() RETURNS trigger
    LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% on append-only table % is not allowed', TG_OP, TG_TABLE_NAME
        USING ERRCODE = 'restrict_violation';
END;
$$;

-- auction, charge, redemption: immutable rows (no UPDATE, no DELETE, no TRUNCATE).
DROP TRIGGER IF EXISTS auction_append_only ON auction;
CREATE TRIGGER auction_append_only BEFORE UPDATE OR DELETE ON auction
    FOR EACH ROW EXECUTE FUNCTION reject_history_rewrite();
DROP TRIGGER IF EXISTS auction_no_truncate ON auction;
CREATE TRIGGER auction_no_truncate BEFORE TRUNCATE ON auction
    FOR EACH STATEMENT EXECUTE FUNCTION reject_history_rewrite();

DROP TRIGGER IF EXISTS charge_append_only ON charge;
CREATE TRIGGER charge_append_only BEFORE UPDATE OR DELETE ON charge
    FOR EACH ROW EXECUTE FUNCTION reject_history_rewrite();
DROP TRIGGER IF EXISTS charge_no_truncate ON charge;
CREATE TRIGGER charge_no_truncate BEFORE TRUNCATE ON charge
    FOR EACH STATEMENT EXECUTE FUNCTION reject_history_rewrite();

DROP TRIGGER IF EXISTS redemption_append_only ON redemption;
CREATE TRIGGER redemption_append_only BEFORE UPDATE OR DELETE ON redemption
    FOR EACH ROW EXECUTE FUNCTION reject_history_rewrite();
DROP TRIGGER IF EXISTS redemption_no_truncate ON redemption;
CREATE TRIGGER redemption_no_truncate BEFORE TRUNCATE ON redemption
    FOR EACH STATEMENT EXECUTE FUNCTION reject_history_rewrite();

-- outbox_event: rows are never deleted, but the dispatcher legitimately UPDATEs
-- delivery state (status, attempts, locked_until), so only DELETE/TRUNCATE are blocked.
DROP TRIGGER IF EXISTS outbox_event_no_delete ON outbox_event;
CREATE TRIGGER outbox_event_no_delete BEFORE DELETE ON outbox_event
    FOR EACH ROW EXECUTE FUNCTION reject_history_rewrite();
DROP TRIGGER IF EXISTS outbox_event_no_truncate ON outbox_event;
CREATE TRIGGER outbox_event_no_truncate BEFORE TRUNCATE ON outbox_event
    FOR EACH STATEMENT EXECUTE FUNCTION reject_history_rewrite();
