-- Rollback for supabase/migrations/20261008100000_company_workbench.sql.
-- Run it in a transaction, newest migration first. The migration file itself is never edited.
-- DATA LOSS: deletes every saved peer group, valuation scenario and investment-case item.
-- Proven by apps/backend/tests/test_lifecycle_phase8.py (needs STEWARD_TEST_DATABASE_URL).

drop table if exists public.company_case_item;
drop table if exists public.company_valuation_scenario;
drop table if exists public.company_peer_group;
drop function if exists public.company_valuation_scenario_immutable();
