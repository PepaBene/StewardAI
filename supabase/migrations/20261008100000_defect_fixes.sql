-- Defects found by the 2026-10-08 production run and drills.
--   * hs_concordance: production already carries source_url / source_sha256 (loaded by hand);
--     the migration history did not.  Added with IF NOT EXISTS so both agree.
--   * disclosure_adjudication: a second label by the same person overwrote the first.  Every
--     overwritten label is now kept in disclosure_adjudication_history.
--   * public.purge_source_documents(regulator): the ordered, guarded deletion of one source's
--     documents (ESEF load back-out), because the evidence ledgers refuse a plain DELETE.
--   * search_path fixed on two trigger functions; the platform's rls_auto_enable() is no longer
--     callable by API roles.
-- price_bar_source_check: production already matches 20261005120000, nothing to change.

alter table public.hs_concordance
  add column if not exists source_url text,
  add column if not exists source_sha256 text;

-- ------------------------------------------------------------------ adjudication history
create table public.disclosure_adjudication_history (
  id bigint generated always as identity primary key,
  adjudication_id bigint not null,
  dataset text not null,
  sample_key text not null,
  item_ref text not null,
  machine_value text,
  human_value text not null,
  correct boolean not null,
  planted_false_positive boolean not null,
  note text,
  adjudicated_by text not null,
  adjudicated_at timestamptz not null,
  superseded_at timestamptz not null default now()
);
create index disclosure_adjudication_history_item
  on public.disclosure_adjudication_history (dataset, sample_key, item_ref);

create function public.keep_adjudication_history() returns trigger
language plpgsql set search_path = '' as $$
begin
  insert into public.disclosure_adjudication_history
    (adjudication_id, dataset, sample_key, item_ref, machine_value, human_value, correct,
     planted_false_positive, note, adjudicated_by, adjudicated_at)
  values (old.id, old.dataset, old.sample_key, old.item_ref, old.machine_value, old.human_value,
          old.correct, old.planted_false_positive, old.note, old.adjudicated_by,
          old.adjudicated_at);
  return new;
end $$;

create trigger keep_history before update on public.disclosure_adjudication
  for each row execute function public.keep_adjudication_history();

create trigger append_only before update or delete on public.disclosure_adjudication_history
  for each row execute function public.forbid_append_only_mutation();

alter table public.disclosure_adjudication_history enable row level security;
revoke all on public.disclosure_adjudication_history from anon, authenticated;
grant select on public.disclosure_adjudication_history to authenticated;
grant select, insert on public.disclosure_adjudication_history to app_backend;
create policy owner_read on public.disclosure_adjudication_history for select to authenticated
  using ((select private.is_owner()));
create policy backend_all on public.disclosure_adjudication_history for all to app_backend
  using (true) with check (true);

-- ---------------------------------------------------------------- delete one source's rows
-- Removes every document of one regulator with its versions and every row that points at them
-- (one level: a deeper foreign key aborts the whole call and names itself).  The append-only
-- guard is lifted for this transaction only.
create function public.purge_source_documents(p_regulator text) returns jsonb
language plpgsql set search_path = '' as $$
declare
  fk record;
  n bigint;
  report jsonb := '{}'::jsonb;
begin
  if coalesce(btrim(p_regulator), '') = '' then
    raise exception 'regulator is required';
  end if;
  perform set_config('steward.allow_append_only_changes', 'on', true);
  create temp table _purge_docs on commit drop as
    select id from public.source_document where regulator = p_regulator;
  create temp table _purge_versions on commit drop as
    select v.id from public.source_document_version v join _purge_docs d on d.id = v.document_id;
  -- rows pointing at a version (other than the version table's own self references)
  for fk in
    select c.conrelid::regclass::text as tbl, a.attname as col
      from pg_constraint c
      join pg_attribute a on a.attrelid = c.conrelid and a.attnum = c.conkey[1]
     where c.contype = 'f' and c.confrelid = 'public.source_document_version'::regclass
       and c.conrelid <> 'public.source_document_version'::regclass
  loop
    execute format('delete from %s where %I in (select id from _purge_versions)', fk.tbl, fk.col);
    get diagnostics n = row_count;
    if n > 0 then report := report || jsonb_build_object(fk.tbl, n); end if;
  end loop;
  update public.source_document_version
     set amends_version_id = null, superseded_by_id = null
   where id in (select id from _purge_versions);
  delete from public.source_document_version where id in (select id from _purge_versions);
  get diagnostics n = row_count;
  report := report || jsonb_build_object('source_document_version', n);
  for fk in
    select c.conrelid::regclass::text as tbl, a.attname as col
      from pg_constraint c
      join pg_attribute a on a.attrelid = c.conrelid and a.attnum = c.conkey[1]
     where c.contype = 'f' and c.confrelid = 'public.source_document'::regclass
       and c.conrelid <> 'public.source_document_version'::regclass
  loop
    execute format('delete from %s where %I in (select id from _purge_docs)', fk.tbl, fk.col);
    get diagnostics n = row_count;
    if n > 0 then report := report || jsonb_build_object(fk.tbl, n); end if;
  end loop;
  delete from public.source_document where id in (select id from _purge_docs);
  get diagnostics n = row_count;
  return report || jsonb_build_object('source_document', n);
end $$;
revoke all on function public.purge_source_documents(text) from public, anon, authenticated;
grant execute on function public.purge_source_documents(text) to app_backend;

-- A version records which parser has read it (ESEF facts); that is bookkeeping, not evidence.
drop trigger append_only on public.source_document_version;
create trigger append_only before update or delete on public.source_document_version
  for each row execute function public.forbid_append_only_mutation(
    'retrieved_at','amends_version_id','superseded_by_id','raw_location','parser_version');

-- ------------------------------------------------------------------------ hygiene
alter function public.ownership_known_at_default() set search_path = '';
do $$
begin
  if to_regprocedure('private.screen_version_immutable()') is not null then
    alter function private.screen_version_immutable() set search_path = '';
  end if;
  if to_regprocedure('public.rls_auto_enable()') is not null then
    revoke execute on function public.rls_auto_enable() from public, anon, authenticated;
  end if;
end $$;
