-- Rollback for supabase/migrations/20261006130000_web_domain_tier.sql.
-- Run it in a transaction, newest migration first. The migration file itself is never edited.
-- DATA LOSS: deletes every learned domain tier, including the owner's decisions on them; domains
-- not named in the gap-research lists are rejected again until they are judged anew.
-- Written by hand and not yet proven by apps/backend/tests/test_lifecycle_phase8.py, which needs
-- a database (STEWARD_TEST_DATABASE_URL); run that test before relying on this file.

drop table if exists public.web_domain_tier;
