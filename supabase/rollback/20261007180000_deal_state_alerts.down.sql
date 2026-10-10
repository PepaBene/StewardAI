-- Rollback for supabase/migrations/20261007180000_deal_state_alerts.sql.
-- Run it in a transaction, newest migration first. The migration file itself is never edited.
-- DATA LOSS: deletes every stored deal_state alert, because the narrower constraint could not hold them.
-- Restores the schema the migration changed, as proven by
-- apps/backend/tests/test_lifecycle_phase8.py::test_each_rollback_restores_the_schema_its_migration_changed
-- (needs a database, STEWARD_TEST_DATABASE_URL). Rehearse on a copy before applying to production.

delete from public.disclosure_alert where kind = 'deal_state';
alter table public.disclosure_alert drop constraint disclosure_alert_kind_check;
alter table public.disclosure_alert add constraint disclosure_alert_kind_check
  check (kind in ('insider_trade', 'major_holder', 'relationship_change'));
