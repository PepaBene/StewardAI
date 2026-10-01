-- Rollback for supabase/migrations/20261004120000_free_sources_verification.sql (plan D8.4).
-- Generated from a before/after diff of the public catalog and proven by
-- apps/backend/tests/test_lifecycle_phase8.py: the migration is applied, this file is run, and the
-- catalog fingerprint (tables, columns, constraints, indexes, functions, policies, grants, seed
-- rows) must equal the one taken before the migration. Run it in a transaction, newest migration
-- first. The migration file itself is never edited.
-- DATA LOSS: drops these tables and every row in them: domain_error_threshold, reconciliation_sample, signal_audit_result, source_shadow_job_snapshot, source_shadow_report, source_shadow_snapshot.

drop table if exists public.domain_error_threshold cascade;
drop table if exists public.reconciliation_sample cascade;
drop table if exists public.signal_audit_result cascade;
drop table if exists public.source_shadow_job_snapshot cascade;
drop table if exists public.source_shadow_report cascade;
drop table if exists public.source_shadow_snapshot cascade;
