-- Rollback for supabase/migrations/20261024130000_holding_risk_tier.sql.
-- Run it in a transaction, newest migration first. The migration file itself is never edited.
-- DATA LOSS: deletes the stored risk tier of each holding (they are worked out again on the next sync).
-- Written by hand and not yet proven against a database.

drop table if exists public.holding_risk_tier;
