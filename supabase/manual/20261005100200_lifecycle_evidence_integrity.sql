-- Run in the Supabase SQL editor on the live project, ONE transaction (all or nothing).
-- Identical to supabase/migrations/20261005100200_lifecycle_evidence_integrity.sql, wrapped in begin/commit.
begin;

-- Audit items B3, B52 and the C-13 leftovers (core stream): lifecycle ledgers, atomic evidence
-- dedupe, append-only guards and covering indexes for every foreign key.
--
-- B3  : issuer_alias / issuer_relationship / security_listing gain `known_at` (when the fact was
--       learned) so as-of readers can honor both the valid window and the knowledge cutoff.
-- C-13: source_evidence quotes are unique per document version (atomic upsert instead of
--       select-then-insert); the evidence ledgers refuse UPDATE/DELETE of recorded evidence.
-- B52 : an index covers the leading columns of every foreign key that had none.

alter table public.security_listing add column if not exists known_at timestamptz not null default now();
alter table public.issuer_alias add column if not exists known_at timestamptz not null default now();
alter table public.issuer_alias add column if not exists valid_to_known_at timestamptz;
comment on column public.issuer_alias.valid_to_known_at is
  'when valid_to was recorded; before that moment an as-of reader treats the window as open';
alter table public.issuer_relationship add column if not exists known_at timestamptz not null default now();
alter table public.issuer_relationship add column if not exists valid_to date;
alter table public.issuer_relationship add constraint issuer_relationship_window_check
  check (valid_to is null or valid_to >= valid_from);
alter table public.issuer_alias add constraint issuer_alias_window_check
  check (valid_to is null or valid_from is null or valid_to >= valid_from);

-- Collapse duplicate evidence quotes (the old select-then-insert could race), repointing every
-- foreign key that referenced a duplicate to the surviving row.
create temp table _evidence_dup on commit drop as
select e.id as dup_id, k.keep_id
  from public.source_evidence e
  join (select document_version_id, md5(quote) as h, min(id) as keep_id
          from public.source_evidence
         where quote is not null and observation_id is null and page is null
           and table_name is null and span_start is null and span_end is null
         group by 1, 2 having count(*) > 1) k
    on k.document_version_id = e.document_version_id and k.h = md5(e.quote)
 where e.quote is not null and e.observation_id is null and e.page is null
   and e.table_name is null and e.span_start is null and e.span_end is null
   and e.id <> k.keep_id;
do $$
declare fk record;
begin
  for fk in
    select c.conrelid::regclass::text as tbl, a.attname as col
      from pg_constraint c
      join pg_attribute a on a.attrelid = c.conrelid and a.attnum = c.conkey[1]
     where c.contype = 'f' and c.confrelid = 'public.source_evidence'::regclass
  loop
    execute format('update %s t set %I = d.keep_id from _evidence_dup d where t.%I = d.dup_id',
                   fk.tbl, fk.col, fk.col);
  end loop;
end $$;
delete from public.source_evidence e using _evidence_dup d where e.id = d.dup_id;
create unique index source_evidence_version_quote on public.source_evidence
  (document_version_id, md5(quote))
  where quote is not null and observation_id is null and page is null and table_name is null
    and span_start is null and span_end is null;

-- Append-only evidence.  Recorded evidence is what point-in-time replay stands on, so the
-- ledgers refuse DELETE and any UPDATE outside a short list of bookkeeping columns.  A
-- deliberate maintenance change must say so: `set local steward.allow_append_only_changes = on`.
create function public.forbid_append_only_mutation() returns trigger
language plpgsql set search_path = '' as $$
begin
  if coalesce(current_setting('steward.allow_append_only_changes', true), '') = 'on' then
    return case tg_op when 'DELETE' then old else new end;
  end if;
  if tg_op = 'DELETE' then
    raise exception '% is append-only: recorded evidence is not deleted', tg_table_name
      using errcode = 'restrict_violation';
  end if;
  -- tg_argv is NULL (not empty) for a trigger declared without arguments.
  if (to_jsonb(new) - coalesce(tg_argv, '{}'::text[]))
     is distinct from (to_jsonb(old) - coalesce(tg_argv, '{}'::text[])) then
    raise exception '% is append-only: only % may change', tg_table_name,
      coalesce(tg_argv, '{}'::text[])
      using errcode = 'restrict_violation';
  end if;
  return new;
end $$;

create trigger append_only before update or delete on public.source_document_version
  for each row execute function public.forbid_append_only_mutation(
    'retrieved_at','amends_version_id','superseded_by_id','raw_location');
create trigger append_only before update or delete on public.source_evidence
  for each row execute function public.forbid_append_only_mutation();
create trigger append_only before update or delete on public.source_record_evidence
  for each row execute function public.forbid_append_only_mutation();
create trigger append_only before update or delete on public.listing_lifecycle_snapshot
  for each row execute function public.forbid_append_only_mutation();
create trigger append_only before update or delete on public.legal_entity_snapshot
  for each row execute function public.forbid_append_only_mutation('valid_to');
create trigger append_only before update or delete on public.macro_payload_archive
  for each row execute function public.forbid_append_only_mutation('payload','storage_location');

-- B52: covering indexes for foreign keys that had none.
create index if not exists fk_account_snapshot_config_version_id on public.account_snapshot (config_version_id);
create index if not exists fk_account_snapshot_job_id on public.account_snapshot (job_id);
create index if not exists fk_alert_breach_id on public.alert (breach_id);
create index if not exists fk_alert_cluster_id on public.alert (cluster_id);
create index if not exists fk_alert_config_version_id on public.alert (config_version_id);
create index if not exists fk_alert_job_id on public.alert (job_id);
create index if not exists fk_alert_verdict_id on public.alert (verdict_id);
create index if not exists fk_chat_action_job_id on public.chat_action (job_id);
create index if not exists fk_chat_action_message_id on public.chat_action (message_id);
create index if not exists fk_chat_message_job_id on public.chat_message (job_id);
create index if not exists fk_config_version_parent_id on public.config_version (parent_id);
create index if not exists fk_coverage_status_last_good_observation_id on public.coverage_status (last_good_observation_id);
create index if not exists fk_etf_snapshot_job_id on public.etf_snapshot (job_id);
create index if not exists fk_eval_candidate_fact_id on public.eval_candidate (fact_id);
create index if not exists fk_eval_candidate_feedback_id on public.eval_candidate (feedback_id);
create index if not exists fk_eval_candidate_news_item_id on public.eval_candidate (news_item_id);
create index if not exists fk_evidence_job_id on public.evidence (job_id);
create index if not exists fk_exposure_mapping_evidence_id on public.exposure_mapping (evidence_id);
create index if not exists fk_exposure_mapping_issuer_id on public.exposure_mapping (issuer_id);
create index if not exists fk_fact_job_id on public.fact (job_id);
create index if not exists fk_fact_restates_fact_id on public.fact (restates_fact_id);
create index if not exists fk_fact_reconciliation_job_id on public.fact_reconciliation (job_id);
create index if not exists fk_fact_review_corrected_fact_id on public.fact_review (corrected_fact_id);
create index if not exists fk_fact_review_job_id on public.fact_review (job_id);
create index if not exists fk_filing_hit_news_item_id on public.filing_hit (news_item_id);
create index if not exists fk_filing_hit_relationship_id on public.filing_hit (relationship_id);
create index if not exists fk_form13f_dataset_job_id on public.form13f_dataset (job_id);
create index if not exists fk_form13f_filing_dataset_url on public.form13f_filing (dataset_url);
create index if not exists fk_form13f_manager_dataset_dataset_url on public.form13f_manager_dataset (dataset_url);
create index if not exists fk_fund_factsheet_snapshot_id on public.fund_factsheet (snapshot_id);
create index if not exists fk_identity_match_decision_candidate_issuer_id on public.identity_match_decision (candidate_issuer_id);
create index if not exists fk_identity_match_decision_candidate_listing_id on public.identity_match_decision (candidate_listing_id);
create index if not exists fk_identity_match_decision_candidate_security_id on public.identity_match_decision (candidate_security_id);
create index if not exists fk_identity_match_decision_instrument_ticker on public.identity_match_decision (instrument_ticker);
create index if not exists fk_identity_match_decision_provider_identifier_id on public.identity_match_decision (provider_identifier_id);
create index if not exists fk_instrument_security_link_listing_id on public.instrument_security_link (listing_id);
create index if not exists fk_instrument_security_link_security_id on public.instrument_security_link (security_id);
create index if not exists fk_issuer_alias_source_document_version_id on public.issuer_alias (source_document_version_id);
create index if not exists fk_issuer_relationship_evidence_id on public.issuer_relationship (evidence_id);
create index if not exists fk_issuer_relationship_successor_id on public.issuer_relationship (successor_id);
create index if not exists fk_news_item_job_id on public.news_item (job_id);
create index if not exists fk_news_item_source_id on public.news_item (source_id);
create index if not exists fk_nport_holding_held_ticker on public.nport_holding (held_ticker);
create index if not exists fk_nport_holding_issuer_id on public.nport_holding (issuer_id);
create index if not exists fk_nport_holding_security_id on public.nport_holding (security_id);
create index if not exists fk_portfolio_breach_config_version_id on public.portfolio_breach (config_version_id);
create index if not exists fk_portfolio_breach_first_job_id on public.portfolio_breach (first_job_id);
create index if not exists fk_portfolio_breach_last_job_id on public.portfolio_breach (last_job_id);
create index if not exists fk_position_snapshot_config_version_id on public.position_snapshot (config_version_id);
create index if not exists fk_position_snapshot_job_id on public.position_snapshot (job_id);
create index if not exists fk_provider_identifier_issuer_id on public.provider_identifier (issuer_id);
create index if not exists fk_provider_identifier_listing_id on public.provider_identifier (listing_id);
create index if not exists fk_provider_identifier_security_id on public.provider_identifier (security_id);
create index if not exists fk_report_job_id on public.report (job_id);
create index if not exists fk_security_issuer_id on public.security (issuer_id);
create index if not exists fk_shares_outstanding_job_id on public.shares_outstanding (job_id);
create index if not exists fk_source_conflict_alternative_observation_id on public.source_conflict (alternative_observation_id);
create index if not exists fk_source_document_version_amends_version_id on public.source_document_version (amends_version_id);
create index if not exists fk_source_document_version_superseded_by_id on public.source_document_version (superseded_by_id);
create index if not exists fk_source_evidence_document_version_id on public.source_evidence (document_version_id);
create index if not exists fk_source_evidence_observation_id on public.source_evidence (observation_id);
create index if not exists fk_source_observation_issuer_id on public.source_observation (issuer_id);
create index if not exists fk_source_observation_listing_id on public.source_observation (listing_id);
create index if not exists fk_source_observation_security_id on public.source_observation (security_id);
create index if not exists fk_source_record_evidence_document_version_id on public.source_record_evidence (document_version_id);
create index if not exists fk_source_record_evidence_evidence_id on public.source_record_evidence (evidence_id);
create index if not exists fk_story_cluster_duplicate_of on public.story_cluster (duplicate_of);
create index if not exists fk_transaction_job_id on public.transaction (job_id);
create index if not exists fk_verdict_config_version_id on public.verdict (config_version_id);
create index if not exists fk_verdict_job_id on public.verdict (job_id);
create index if not exists fk_xbrl_filing_job_id on public.xbrl_filing (job_id);

commit;
