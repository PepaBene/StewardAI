-- Rollback for supabase/migrations/20261008200000_disclosure_audit_fixes.sql.
-- Run it in a transaction, newest migration first. The migration file itself is never edited.
-- DATA LOSS: deal_state labels and suggestions, who made each label (adjudicator_kind), the
-- backfill attempts, the 8-K item index and all but the latest decision per deal party are
-- deleted. The narrower identities fail if two rows now differ only by period start, unit or
-- currency; delete those first. purge_source_documents returns to its one-level form.
-- Restores the schema the migration changed, as proven by
-- apps/backend/tests/test_lifecycle_phase8.py (needs STEWARD_TEST_DATABASE_URL).
select set_config('steward.allow_append_only_changes', 'on', true);

create or replace function public.purge_source_documents(p_regulator text) returns jsonb
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
drop function if exists public.purge_rows(regclass, bigint[], integer, jsonb);
drop table if exists public.disclosure_backfill_attempt;
drop table if exists public.sec_filing_item;

-- Only the latest decision per (participant, issuer) survives the older unique key.
drop trigger if exists append_only on public.deal_participant_decision;
drop index if exists public.deal_participant_decision_latest;
delete from public.deal_participant_decision d
 using public.deal_participant_decision n
 where n.participant_id = d.participant_id and n.issuer_id = d.issuer_id
   and (n.decided_at, n.id) > (d.decided_at, d.id);
alter table public.deal_participant_decision
  add constraint deal_participant_decision_participant_id_issuer_id_key
  unique (participant_id, issuer_id);

delete from public.disclosure_adjudication_suggestion where dataset = 'deal_state';
alter table public.disclosure_adjudication_suggestion
  drop constraint disclosure_adjudication_suggestion_dataset_check;
alter table public.disclosure_adjudication_suggestion
  add constraint disclosure_adjudication_suggestion_dataset_check
  check (dataset in ('insider_class', 'relationship_claim', 'ownership_match', 'statement_note',
                     'document_sample'));
alter table public.disclosure_adjudication disable trigger keep_history;
delete from public.disclosure_adjudication where dataset = 'deal_state';
alter table public.disclosure_adjudication enable trigger keep_history;
alter table public.disclosure_adjudication drop constraint disclosure_adjudication_dataset_check;
alter table public.disclosure_adjudication add constraint disclosure_adjudication_dataset_check
  check (dataset in ('insider_class', 'relationship_claim', 'ownership_match', 'statement_note',
                     'document_sample'));

create or replace function public.keep_adjudication_history() returns trigger
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
alter table public.disclosure_adjudication_history drop column adjudicator_kind;
alter table public.disclosure_adjudication drop column adjudicator_kind;

drop index public.estimate_value_identity;
create unique index estimate_value_identity on public.estimate_value
  (snapshot_id, metric, coalesce(segment, ''), period_end, basis);

drop index public.disclosure_observation_identity;
create unique index disclosure_observation_identity on public.disclosure_observation
  (document_version_id, metric, coalesce(segment, ''), period_end, md5(dimensions::text),
   parser_version);
