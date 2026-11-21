-- A charge can be capped at the campaign's budget left (reason 'budget_cap'):
-- a brand never pays more than it budgeted. 0002 is applied, so this widens the CHECK.
ALTER TABLE charge DROP CONSTRAINT charge_reason_check;
ALTER TABLE charge ADD CONSTRAINT charge_reason_check
    CHECK (reason IN ('attention', 'no_attention', 'budget_cap'));
