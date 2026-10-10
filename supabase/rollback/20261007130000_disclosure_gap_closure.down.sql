-- Rollback for supabase/migrations/20261007130000_disclosure_gap_closure.sql.
-- Run it in a transaction, newest migration first. The migration file itself is never edited.
-- DATA LOSS: drops disclosure_alert (stored alerts and acknowledgements), owner_dependency_weight
-- (every weight an owner assigned and its note), procurement_award_detail, the 13D/G exhibit
-- table, disclosure_adjudication (every sampled label), and the added columns on
-- relationship_disclosure and form13f_filing. The denominator backfill is not undone: it only
-- filled a column that this migration's predecessor created and that was null.
-- Restores the schema the migration changed, as proven by
-- apps/backend/tests/test_lifecycle_phase8.py::test_each_rollback_restores_the_schema_its_migration_changed
-- (needs a database, STEWARD_TEST_DATABASE_URL). Rehearse on a copy before applying to production.

drop table if exists public.disclosure_adjudication;
drop table if exists public.control_stake_exhibit;

alter table public.form13f_filing
  drop column if exists omitted_holdings_notice,
  drop column if exists reported_total_value_usd,
  drop column if exists reported_line_count;

drop table if exists public.procurement_award_detail;
drop table if exists public.owner_dependency_weight;
alter table public.relationship_disclosure drop column if exists claim_label;

drop table if exists public.disclosure_alert;
