-- Rollback for supabase/migrations/20261015100000_missing_fk_indexes.sql.
-- Run it in a transaction, newest migration first. The migration file itself is never edited.
-- No data loss: only indexes are dropped.
-- Proven by apps/backend/tests/test_lifecycle_phase8.py (needs STEWARD_TEST_DATABASE_URL).

drop index if exists public.company_case_item_owner_idx;
drop index if exists public.company_report_version_research_report_idx;
drop index if exists public.screen_run_screen_idx;
drop index if exists public.screen_run_universe_snapshot_idx;
