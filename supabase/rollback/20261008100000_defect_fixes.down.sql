-- Rollback for supabase/migrations/20261008100000_defect_fixes.sql.
-- Run it in a transaction, newest migration first. The migration file itself is never edited.
-- DATA LOSS: adjudication history rows are dropped with the table.
-- hs_concordance.source_url / source_sha256 are dropped only while they hold nothing: production
-- loaded them by hand before this migration, so filled columns are kept. The search path of
-- private.screen_version_immutable() and the revoke on public.rls_auto_enable() are kept too
-- (platform functions; undoing them would re-open a security warning).
-- Restores the schema the migration changed, as proven by
-- apps/backend/tests/test_lifecycle_phase8.py (needs STEWARD_TEST_DATABASE_URL).
drop function if exists public.purge_source_documents(text);
drop trigger if exists append_only on public.source_document_version;
create trigger append_only before update or delete on public.source_document_version
  for each row execute function public.forbid_append_only_mutation(
    'retrieved_at','amends_version_id','superseded_by_id','raw_location');
drop trigger if exists keep_history on public.disclosure_adjudication;
drop function if exists public.keep_adjudication_history();
drop table if exists public.disclosure_adjudication_history;
alter function public.ownership_known_at_default() reset search_path;
do $$
begin
  if not exists (select 1 from public.hs_concordance
                  where source_url is not null or source_sha256 is not null) then
    alter table public.hs_concordance drop column source_url, drop column source_sha256;
  end if;
end $$;
