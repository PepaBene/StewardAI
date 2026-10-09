-- Rollback for supabase/migrations/20261018100000_decision_saved_kinds.sql.
-- Run it in a transaction, newest migration first. The migration file itself is never edited.
-- DATA LOSS: deletes every saved shock scenario, what-if case, rebalance plan and theme exposure,
-- because the narrower kind check cannot hold them.
-- Proven by apps/backend/tests/test_lifecycle_phase8.py (needs STEWARD_TEST_DATABASE_URL).

delete from public.saved_analysis
  where kind in ('shock_scenario', 'whatif_case', 'rebalance_plan', 'theme_exposure');

alter table public.saved_analysis drop constraint saved_analysis_kind_check;
alter table public.saved_analysis add constraint saved_analysis_kind_check
  check (kind in ('xray_scenario', 'performance_report'));

comment on table public.saved_analysis is
  'Owner-saved X-Ray what-if scenarios and performance report snapshots; archived, never deleted.';
