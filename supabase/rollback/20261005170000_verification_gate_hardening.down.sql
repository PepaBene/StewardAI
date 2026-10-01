-- Rollback for supabase/migrations/20261005170000_verification_gate_hardening.sql (plan D8.4).
-- Generated from a before/after diff of the public catalog and proven by
-- apps/backend/tests/test_lifecycle_phase8.py: the migration is applied, this file is run, and the
-- catalog fingerprint (tables, columns, constraints, indexes, functions, policies, grants, seed
-- rows) must equal the one taken before the migration. Run it in a transaction, newest migration
-- first. The migration file itself is never edited.
-- DATA NOTE: only constraints, an index and one seeded quota are removed. The duplicate
-- reconciliation samples the forward migration collapsed to the newest one are NOT restored.

alter table public.reconciliation_sample drop constraint if exists reconciliation_sample_not_future;
alter table public.reconciliation_sample drop constraint if exists reconciliation_sample_reviewer_named;
alter table public.reconciliation_sample drop constraint if exists reconciliation_sample_source_named;
alter table public.signal_audit_result drop constraint if exists signal_audit_result_not_future;
alter table public.signal_audit_result drop constraint if exists signal_audit_result_reviewer_named;
drop index if exists public.reconciliation_sample_unique_key;
update public.provider_access set quota = null, quota_period = null
 where provider = 'fmp' and endpoint = 'sector_snapshot' and quota = 250 and quota_period = 'day';
