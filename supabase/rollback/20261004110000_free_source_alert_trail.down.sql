-- Rollback for supabase/migrations/20261004110000_free_source_alert_trail.sql (plan D8.4).
-- Generated from a before/after diff of the public catalog and proven by
-- apps/backend/tests/test_lifecycle_phase8.py: the migration is applied, this file is run, and the
-- catalog fingerprint (tables, columns, constraints, indexes, functions, policies, grants, seed
-- rows) must equal the one taken before the migration. Run it in a transaction, newest migration
-- first. The migration file itself is never edited.
-- DATA LOSS: drops these tables and every row in them: free_source_alert_state, free_source_alert_trail (the owner-readable alert decision trail and the patent-status / watermark state the evaluator compares against).

drop table if exists public.free_source_alert_state cascade;
drop table if exists public.free_source_alert_trail cascade;
