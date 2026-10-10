-- Rollback for supabase/migrations/20261026100000_tail_snapshot_kind.sql.
-- Run it in a transaction, newest migration first. The migration file itself is never edited.
-- DATA LOSS: deletes the saved tail-risk reports (kind 'tail'); the next sync saves a new one.
-- Proven by apps/backend/tests/test_lifecycle_phase8.py (needs STEWARD_TEST_DATABASE_URL).

delete from public.risk_report_snapshot where kind = 'tail';
alter table public.risk_report_snapshot drop constraint risk_report_snapshot_kind_check;
alter table public.risk_report_snapshot add constraint risk_report_snapshot_kind_check
  check (kind in ('ex_ante', 'replay', 'factors', 'shock'));
