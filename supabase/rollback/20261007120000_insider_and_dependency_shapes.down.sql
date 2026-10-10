-- Rollback for supabase/migrations/20261007120000_insider_and_dependency_shapes.sql.
-- Run it in a transaction, newest migration first. The migration file itself is never edited.
-- DATA LOSS: drops insider_classification_override, so every reviewer override and its reason is
-- lost and insider rows return to the deterministic classifier. Dropping claim_shape,
-- counterparty_count, denominator_kind and period_end loses what a reviewer or extractor recorded
-- there; known_at on insider_filing is derived (end of filing day UTC) and loses nothing `filed`
-- does not hold.
-- Restores the schema the migration changed, as proven by
-- apps/backend/tests/test_lifecycle_phase8.py::test_each_rollback_restores_the_schema_its_migration_changed
-- (needs a database, STEWARD_TEST_DATABASE_URL). Rehearse on a copy before applying to production.

alter table public.relationship_disclosure drop constraint if exists relationship_disclosure_group_count;
alter table public.relationship_disclosure drop constraint if exists relationship_disclosure_shape_ok;
alter table public.relationship_disclosure
  drop column if exists period_end,
  drop column if exists denominator_kind,
  drop column if exists counterparty_count,
  drop column if exists claim_shape;

drop table if exists public.insider_classification_override;

drop index if exists public.insider_filing_known;
drop trigger if exists insider_filing_known_at on public.insider_filing;
alter table public.insider_filing drop column if exists known_at;
