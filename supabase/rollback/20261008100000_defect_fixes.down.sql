-- Rolls back 20261008100000_defect_fixes.  Adjudication history rows are dropped with the table.
-- hs_concordance columns are left in place: production held them before this migration.
drop function if exists public.purge_source_documents(text);
drop trigger if exists append_only on public.source_document_version;
create trigger append_only before update or delete on public.source_document_version
  for each row execute function public.forbid_append_only_mutation(
    'retrieved_at','amends_version_id','superseded_by_id','raw_location');
drop trigger if exists keep_history on public.disclosure_adjudication;
drop function if exists public.keep_adjudication_history();
drop table if exists public.disclosure_adjudication_history;
alter function public.ownership_known_at_default() reset search_path;
