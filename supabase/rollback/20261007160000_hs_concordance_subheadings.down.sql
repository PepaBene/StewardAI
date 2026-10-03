-- Rollback for supabase/migrations/20261007160000_hs_concordance_subheadings.sql (plan D8.4).
-- Proven by apps/backend/tests/test_lifecycle_phase8.py: the migration is applied, this file is
-- run, and the catalog fingerprint (tables, columns, constraints, indexes, functions, policies,
-- grants, seed rows) must equal the one taken before the migration. Run it in a transaction,
-- newest migration first. The migration file itself is never edited.
-- DATA LOSS: deletes every hs_concordance row that carries a source_url (the 6-digit UNSD rows
-- seeded by the migration or loaded since by `steward.reference.hs_concordance`); the 2-digit
-- 'chapter' rows are kept. Re-run the loader to restore them.

delete from public.hs_concordance where source_url is not null;
drop index if exists public.hs_concordance_to_code;
alter table public.hs_concordance drop column if exists source_sha256;
alter table public.hs_concordance drop column if exists source_url;
