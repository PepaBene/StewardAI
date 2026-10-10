-- The Companies House and EDINET collectors link each stored filing to its source record under
-- the domain table `holding_filing_event`, but source_record_evidence_domain_table_check never
-- listed that name, so the first insert (AstraZeneca's Companies House filings) failed and the
-- whole companies_house_filings job with it.
--
-- Rebuilt from the live constraint plus the new name, as the earlier extensions do, so other
-- migrations that extended the list are not clobbered.

do $$
declare existing text[]; wanted text[] := array['holding_filing_event'];
begin
  select array(select m[1] from regexp_matches(pg_get_constraintdef(oid), '''([a-z0-9_]+)''', 'g') as m)
    into existing
    from pg_constraint
   where conrelid = 'public.source_record_evidence'::regclass
     and conname = 'source_record_evidence_domain_table_check';
  alter table public.source_record_evidence drop constraint source_record_evidence_domain_table_check;
  execute format(
    'alter table public.source_record_evidence add constraint source_record_evidence_domain_table_check check (domain_table in (%s))',
    (select string_agg(quote_literal(t), ',' order by t)
       from (select distinct unnest(coalesce(existing, '{}') || wanted) as t) u));
end $$;
