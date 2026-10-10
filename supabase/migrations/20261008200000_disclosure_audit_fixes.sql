-- Fixes from docs/revision/public_disclosure_audit_2026-10-08.md.
--   * An observation's identity includes its period start, unit and currency, so a 10-Q's 3-month
--     and 9-month values for one metric no longer collide (audit 3.1).
--   * An estimate value's identity includes its period start, so FY and Q4 lines that end on the
--     same day are two values (audit 3.2).
--   * A label says who made it: a person, a person accepting a model suggestion, or a model. The
--     release gate reads only the first two (audit 2.1, 2.2). Deal states get their own dataset
--     (audit 2.4).
--   * purge_source_documents follows references to any depth (audit 4.32).

-- -------------------------------------------------------------------- observation identity
drop index public.disclosure_observation_identity;
create unique index disclosure_observation_identity on public.disclosure_observation
  (document_version_id, metric, coalesce(segment, ''), coalesce(period_start, date '0001-01-01'),
   period_end, unit, coalesce(currency, ''), md5(dimensions::text), parser_version);

-- ----------------------------------------------------------------------- estimate identity
drop index public.estimate_value_identity;
create unique index estimate_value_identity on public.estimate_value
  (snapshot_id, metric, coalesce(segment, ''), coalesce(period_start, date '0001-01-01'),
   period_end, basis);

-- ------------------------------------------------------------------------ who labelled it
alter table public.disclosure_adjudication
  add column adjudicator_kind text not null default 'person'
    check (adjudicator_kind in ('person', 'accepted_suggestion', 'model'));
alter table public.disclosure_adjudication_history
  add column adjudicator_kind text not null default 'person';

-- Labels written by a model before this column existed say so in their author.
alter table public.disclosure_adjudication disable trigger keep_history;
update public.disclosure_adjudication set adjudicator_kind = 'model'
 where adjudicated_by ilike '%model%' and adjudicated_by ilike '%not a human%';
update public.disclosure_adjudication set adjudicator_kind = 'accepted_suggestion'
 where adjudicator_kind = 'person' and note ilike 'accepted the model suggestion%';
alter table public.disclosure_adjudication enable trigger keep_history;

create or replace function public.keep_adjudication_history() returns trigger
language plpgsql set search_path = '' as $$
begin
  insert into public.disclosure_adjudication_history
    (adjudication_id, dataset, sample_key, item_ref, machine_value, human_value, correct,
     planted_false_positive, note, adjudicated_by, adjudicated_at, adjudicator_kind)
  values (old.id, old.dataset, old.sample_key, old.item_ref, old.machine_value, old.human_value,
          old.correct, old.planted_false_positive, old.note, old.adjudicated_by,
          old.adjudicated_at, old.adjudicator_kind);
  return new;
end $$;

alter table public.disclosure_adjudication drop constraint disclosure_adjudication_dataset_check;
alter table public.disclosure_adjudication add constraint disclosure_adjudication_dataset_check
  check (dataset in ('insider_class', 'relationship_claim', 'ownership_match', 'statement_note',
                     'document_sample', 'deal_state'));
alter table public.disclosure_adjudication_suggestion
  drop constraint disclosure_adjudication_suggestion_dataset_check;
alter table public.disclosure_adjudication_suggestion
  add constraint disclosure_adjudication_suggestion_dataset_check
  check (dataset in ('insider_class', 'relationship_claim', 'ownership_match', 'statement_note',
                     'document_sample', 'deal_state'));

-- ------------------------------------------------------------------ backfill attempts
-- A filing the backfill read and that gave nothing (or failed) is not read again by the same
-- parser, so one stubborn filing cannot hold a slot every run (audit 4.29). A new parser
-- version tries again.
create table public.disclosure_backfill_attempt (
  version_id bigint not null references public.source_document_version(id) on delete cascade,
  step text not null check (step in ('sec_backfill', 'esef_facts')),
  parser_version text not null,
  outcome text not null check (outcome in ('empty', 'failed')),
  detail text,
  attempts integer not null default 1 check (attempts >= 1),
  attempted_at timestamptz not null default now(),
  primary key (version_id, step, parser_version)
);
alter table public.disclosure_backfill_attempt enable row level security;
revoke all on public.disclosure_backfill_attempt from anon, authenticated;
grant select on public.disclosure_backfill_attempt to authenticated;
grant select, insert, update, delete on public.disclosure_backfill_attempt to app_backend;
create policy owner_read on public.disclosure_backfill_attempt for select to authenticated
  using ((select private.is_owner()));
create policy backend_all on public.disclosure_backfill_attempt for all to app_backend
  using (true) with check (true);

-- ------------------------------------------------------------------- 8-K item index
-- The items each 8-K declares, read from the submissions listing the ingest job already fetches.
-- An Item 2.02 8-K is the earnings release: a surprise compares consensus published before it,
-- not before the later 10-Q/10-K (audit 3.5). Only the listing is stored, never the document.
create table public.sec_filing_item (
  accession text primary key,
  issuer_id bigint not null references public.issuer(id) on delete cascade,
  form text not null,
  items text not null,
  report_date date,
  filed_at date not null,
  known_at timestamptz not null,
  first_seen_at timestamptz not null default now()
);
create index sec_filing_item_issuer on public.sec_filing_item (issuer_id, known_at);
alter table public.sec_filing_item enable row level security;
revoke all on public.sec_filing_item from anon, authenticated;
grant select on public.sec_filing_item to authenticated;
grant select, insert, update, delete on public.sec_filing_item to app_backend;
create policy owner_read on public.sec_filing_item for select to authenticated
  using ((select private.is_owner()));
create policy backend_all on public.sec_filing_item for all to app_backend
  using (true) with check (true);

-- ---------------------------------------------------------------- deal party decisions
-- Every decision is kept, so a replay before a later rejection still sees the earlier
-- confirmation (audit 4.2). The latest decision by `decided_at` is in force.
alter table public.deal_participant_decision
  drop constraint deal_participant_decision_participant_id_issuer_id_key;
create index deal_participant_decision_latest
  on public.deal_participant_decision (participant_id, decided_at desc, id desc);
create trigger append_only before update or delete on public.deal_participant_decision
  for each row execute function public.forbid_append_only_mutation();

-- ------------------------------------------------------------- delete one source, any depth
-- Deletes the given rows of a table after every row that references them, depth first. A
-- self reference is cleared instead of followed. A referencing table without an integer `id` is
-- a leaf and is deleted directly; if something references it the call aborts and names it.
create function public.purge_rows(p_table regclass, p_ids bigint[], p_depth integer,
                                  p_report jsonb) returns jsonb
language plpgsql set search_path = '' as $$
declare
  fk record;
  child bigint[];
  n bigint;
  report jsonb := p_report;
begin
  if coalesce(array_length(p_ids, 1), 0) = 0 then
    return report;
  end if;
  if p_depth > 8 then
    raise exception 'purge: references deeper than 8 levels below %', p_table;
  end if;
  for fk in
    select c.conrelid::regclass as tbl, a.attname as col, c.conrelid = c.confrelid as self_ref,
           exists (select 1 from pg_attribute x
                    where x.attrelid = c.conrelid and x.attname = 'id' and not x.attisdropped
                      and x.atttypid in ('int8'::regtype, 'int4'::regtype)) as has_id
      from pg_constraint c
      join pg_attribute a on a.attrelid = c.conrelid and a.attnum = c.conkey[1]
     where c.contype = 'f' and c.confrelid = p_table and array_length(c.conkey, 1) = 1
     order by c.conrelid::regclass::text, a.attname
  loop
    if fk.self_ref then
      execute format('update %s set %I = null where %I = any($1)', fk.tbl, fk.col, fk.col)
        using p_ids;
    elsif fk.has_id then
      execute format('select coalesce(array_agg(id::bigint), ''{}'') from %s where %I = any($1)',
                     fk.tbl, fk.col)
        into child using p_ids;
      report := public.purge_rows(fk.tbl, child, p_depth + 1, report);
    else
      execute format('delete from %s where %I = any($1)', fk.tbl, fk.col) using p_ids;
      get diagnostics n = row_count;
      if n > 0 then
        report := report || jsonb_build_object(
          fk.tbl::text, coalesce((report ->> fk.tbl::text)::bigint, 0) + n);
      end if;
    end if;
  end loop;
  execute format('delete from %s where id = any($1)', p_table) using p_ids;
  get diagnostics n = row_count;
  return report || jsonb_build_object(
    p_table::text, coalesce((report ->> p_table::text)::bigint, 0) + n);
end $$;
revoke all on function public.purge_rows(regclass, bigint[], integer, jsonb)
  from public, anon, authenticated;
grant execute on function public.purge_rows(regclass, bigint[], integer, jsonb) to app_backend;

create or replace function public.purge_source_documents(p_regulator text) returns jsonb
language plpgsql set search_path = '' as $$
declare
  ids bigint[];
begin
  if coalesce(btrim(p_regulator), '') = '' then
    raise exception 'regulator is required';
  end if;
  perform set_config('steward.allow_append_only_changes', 'on', true);
  select coalesce(array_agg(id), '{}') into ids
    from public.source_document where regulator = p_regulator;
  return public.purge_rows('public.source_document'::regclass, ids, 0, '{}'::jsonb);
end $$;
revoke all on function public.purge_source_documents(text) from public, anon, authenticated;
grant execute on function public.purge_source_documents(text) to app_backend;
