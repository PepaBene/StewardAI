-- Rollback for supabase/migrations/20261029100000_news_primary_sources.sql.
-- Run it in a transaction, newest migration first. The migration file itself is never edited.
-- Sources of the types tdnet, hkex, cninfo, dart, gazette and filings must be deleted first,
-- or the restored constraint fails loudly.
-- Proven by apps/backend/tests/test_lifecycle_phase8.py (needs STEWARD_TEST_DATABASE_URL).
-- PRECONDITION: no source may use a type the restored CHECK constraint (source_type_check)
-- forbids; the rollback stops with a clear error before changing anything if one does.
do $$ begin
  if exists (
    select 1 from public.source
    where type in ('tdnet', 'hkex', 'cninfo', 'dart', 'gazette', 'filings')
  ) then
    raise exception 'rollback precondition failed: public.source holds rows of type tdnet, hkex, cninfo, dart, gazette or filings; delete them first';
  end if;
end $$;

alter table public.source drop constraint if exists source_type_check;
alter table public.source add constraint source_type_check
  check (type in ('rss','page','edgar','finnhub','google_news','alpha_vantage','reference',
                  'macro','edgar_fts','rns','eqs','news_sitemap','gdelt','ai_search'));
