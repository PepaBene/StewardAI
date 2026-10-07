-- Rollback for supabase/migrations/20261007190000_disclosure_alert_rules.sql.
-- Run it in a transaction, newest migration first. The migration file itself is never edited.
-- DATA LOSS: deletes every stored alert of the four new kinds and every estimate_value review task
-- (with its audit events), because the narrower constraints could not hold them, and drops why_fired.
-- Restores the schema the migration changed, as proven by
-- apps/backend/tests/test_lifecycle_phase8.py::test_each_rollback_restores_the_schema_its_migration_changed
-- (needs a database, STEWARD_TEST_DATABASE_URL). Rehearse on a copy before applying to production.

delete from public.quality_review_event
 where review_id in (select id from public.quality_review where subject_type = 'estimate_value');
delete from public.quality_review where subject_type = 'estimate_value';
alter table public.quality_review drop constraint quality_review_subject_type_check;
alter table public.quality_review add constraint quality_review_subject_type_check
  check (subject_type in ('claim','observation','document_version','identity_mapping'));

drop index public.disclosure_alert_throttle;
alter table public.disclosure_alert drop column why_fired;

delete from public.disclosure_alert
 where kind in ('controlling_stake', 'insider_buy_cluster', 'estimate_revision',
                'debt_maturity_change');
alter table public.disclosure_alert drop constraint disclosure_alert_kind_check;
alter table public.disclosure_alert add constraint disclosure_alert_kind_check
  check (kind in ('insider_trade', 'major_holder', 'relationship_change', 'deal_state'));
