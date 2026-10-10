-- Rollback for supabase/migrations/20261028130000_filing_event_evidence_domain.sql.
-- Run it in a transaction, newest migration first. The migration file itself is never edited.
-- PRECONDITION: no row may use `holding_filing_event`; the rollback stops with a clear error
-- before changing anything if one does (delete those evidence links first).
-- Proven by apps/backend/tests/test_lifecycle_phase8.py (needs STEWARD_TEST_DATABASE_URL).
do $$ begin
  if exists (select 1 from public.source_record_evidence where domain_table = 'holding_filing_event') then
    raise exception 'rollback precondition failed: public.source_record_evidence holds holding_filing_event rows; delete them first';
  end if;
end $$;
do $$
declare kept text[];
begin
  select array(select m[1] from regexp_matches(pg_get_constraintdef(oid), '''([a-z0-9_]+)''', 'g') as m)
    into kept
    from pg_constraint
   where conrelid = 'public.source_record_evidence'::regclass
     and conname = 'source_record_evidence_domain_table_check';
  alter table public.source_record_evidence drop constraint source_record_evidence_domain_table_check;
  execute format(
    'alter table public.source_record_evidence add constraint source_record_evidence_domain_table_check check (domain_table in (%s))',
    (select string_agg(quote_literal(t), ',' order by t)
       from (select distinct unnest(kept) as t) u where t <> 'holding_filing_event'));
end $$;
