-- Rollback for supabase/migrations/20261010100000_morningstar_gap_closure.sql.
-- Run it in a transaction, newest migration first. The migration file itself is never edited.
-- DATA LOSS: deletes every owner segment mapping, company report version, saved fund comparison
-- and NAV point, and the fund facts that use the keys this migration added.
-- Proven by apps/backend/tests/test_lifecycle_phase8.py (needs STEWARD_TEST_DATABASE_URL).

drop table if exists public.company_segment_map;
drop table if exists public.company_report_version;
drop table if exists public.fund_comparison_saved;
drop table if exists public.fund_nav_point;

delete from public.fund_owner_fact where key in (
  'benchmark_symbol', 'benchmark_currency', 'benchmark_basis',
  'average_maturity_years', 'effective_duration_years', 'credit_quality_note');
alter table public.fund_owner_fact drop constraint fund_owner_fact_key_check;
alter table public.fund_owner_fact add constraint fund_owner_fact_key_check check (key in (
  'replication_method', 'securities_lending', 'lending_revenue_share', 'inception_date',
  'domicile', 'distribution_tax_note', 'liquidity_note', 'tracking_index', 'ucits'));
