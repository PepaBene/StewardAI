-- All remaining migrations 20261005100100 .. 20261005170000 in ONE transaction (all or nothing).
-- Run in the Supabase SQL editor on the live project AFTER 20261005100000_provider_state_latch_remaining.sql.
begin;

-- ===== 20261005100100_observation_identity_nulls.sql =====
-- Audit items A8 and B6 (core stream).
--
-- A8: source_observation_identity omitted period_start, unit, currency and the dimensions, and
--     `revision` was never incremented, so restatements, segment variants and quarter-vs-YTD
--     rows that share a period_end were silently dropped.  The identity now is
--     (domain, metric, issuer/security/listing, geography, period_start, period_end, unit,
--     currency, dimensions hash, source, vintage, revision).  The new index is strictly finer
--     than the old one, so existing rows satisfy it.
-- B6: UNIQUE constraints over nullable columns treated NULLs as distinct, so `on conflict do
--     nothing` never deduplicated (listing_lifecycle_snapshot with no publication_date, a
--     corporate action without a listing, ...).  They become NULLS NOT DISTINCT (PostgreSQL 15+,
--     the Supabase baseline); the column lists, and so every `on conflict (...)` inference, are
--     unchanged.  Existing duplicates of unreferenced tables are removed first (lowest id kept).

drop index public.source_observation_identity;
create unique index source_observation_identity on public.source_observation
  (domain, metric, coalesce(issuer_id,0), coalesce(security_id,0), coalesce(listing_id,0),
   coalesce(geography,''), period_start, period_end, unit, coalesce(currency,''),
   md5(normalized::text), source, coalesce(vintage,''), revision);
comment on index public.source_observation_identity is
  'identity of an observation; normalized holds the dimensions; revision counts restatements';

delete from public.listing_lifecycle_snapshot d using public.listing_lifecycle_snapshot k
 where d.id > k.id and d.isin = k.isin and d.mic = k.mic and d.content_hash = k.content_hash
   and d.publication_date is not distinct from k.publication_date;
delete from public.issuer_alias d using public.issuer_alias k
 where d.id > k.id and d.issuer_id = k.issuer_id and d.name = k.name
   and d.valid_from is not distinct from k.valid_from;
delete from public.filing_hit d using public.filing_hit k
 where d.id > k.id and d.ticker = k.ticker and d.kind = k.kind and d.matched_term = k.matched_term
   and d.accession = k.accession and d.file is not distinct from k.file;
delete from public.corporate_action d using (
  select id, row_number() over (
           partition by listing_id, action_type, ex_date, source, source_url
           order by (status = 'suggested'), id) rn
    from public.corporate_action) r
 where d.id = r.id and r.rn > 1;
delete from public.source_conflict d using public.source_conflict k
 where d.id > k.id and d.alternative_observation_id = k.alternative_observation_id
   and d.preferred_observation_id is not distinct from k.preferred_observation_id;

-- security_listing and provider_identifier are referenced by other tables and have no writer
-- yet; if either held duplicates the statements below fail loudly rather than lose links.
do $$
declare spec text[]; begin
  foreach spec slice 1 in array array[
    array['listing_lifecycle_snapshot','listing_lifecycle_snapshot_isin_mic_publication_date_conten_key',
          'isin, mic, publication_date, content_hash'],
    array['issuer_alias','issuer_alias_issuer_id_name_valid_from_key','issuer_id, name, valid_from'],
    array['security_listing','security_listing_security_id_mic_ticker_valid_from_key',
          'security_id, mic, ticker, valid_from'],
    array['provider_identifier','provider_identifier_provider_identifier_type_identifier_val_key',
          'provider, identifier_type, identifier, valid_from'],
    array['corporate_action','corporate_action_listing_id_action_type_ex_date_source_sour_key',
          'listing_id, action_type, ex_date, source, source_url'],
    array['filing_hit','filing_hit_ticker_kind_matched_term_accession_file_key',
          'ticker, kind, matched_term, accession, file']
  ] loop
    execute format('alter table public.%I drop constraint %I', spec[1], spec[2]);
    execute format('alter table public.%I add constraint %I unique nulls not distinct (%s)',
                   spec[1], spec[2], spec[3]);
  end loop;
end $$;

-- source_conflict_pair is a plain unique index (not a constraint).
drop index public.source_conflict_pair;
create unique index source_conflict_pair on public.source_conflict
  (preferred_observation_id, alternative_observation_id) nulls not distinct;

-- ===== 20261005100200_lifecycle_evidence_integrity.sql =====
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

-- ===== 20261005110000_macro_trade_correctness.sql =====
-- Macro/trade data correctness (audit B10, B14, B16, A18, HS revisions).
--
-- * trade_observation: provider vintage + revision (a changed value is the next revision, an
--   unchanged one is not re-inserted), net weight apart from quantity, ISO alpha-2
--   `reporter_iso` / `partner_iso` populated at write time (Comtrade reports UN M49 numbers; the
--   exposure mappings and alerts use ISO), the re-export / re-import flows, and canonical HS
--   revision labels.
-- * exposure_mapping: the 100% cap is checked per (issuer, dimension) - geography, HS product,
--   or geography+HS cells - so a geography cut and a product cut can both be entered; an HS code
--   needs its revision.
-- * hs_concordance: HS revision concordance (seeded at chapter level only; the WCO correlation
--   tables are loaded through steward.macro.hs.concordance_rows).

create function public.normalize_hs_revision(label text) returns text
language sql immutable set search_path='' as $$
  select case
    when label is null or btrim(label) = '' then null
    when upper(replace(btrim(label), ' ', '')) = 'H0' then 'HS1992'
    when upper(replace(btrim(label), ' ', '')) = 'H1' then 'HS1996'
    when upper(replace(btrim(label), ' ', '')) = 'H2' then 'HS2002'
    when upper(replace(btrim(label), ' ', '')) = 'H3' then 'HS2007'
    when upper(replace(btrim(label), ' ', '')) = 'H4' then 'HS2012'
    when upper(replace(btrim(label), ' ', '')) = 'H5' then 'HS2017'
    when upper(replace(btrim(label), ' ', '')) = 'H6' then 'HS2022'
    when upper(replace(btrim(label), ' ', '')) ~ '^(HS)?(1992|1996|2002|2007|2012|2017|2022)$'
      then 'HS' || substring(upper(replace(btrim(label), ' ', '')) from '(1992|1996|2002|2007|2012|2017|2022)$')
    else upper(replace(btrim(label), ' ', ''))
  end
$$;

alter table public.trade_observation
  add column net_weight numeric,
  add column reporter_iso text,
  add column partner_iso text,
  add column vintage text,
  add column revision integer not null default 0 check (revision >= 0);
alter table public.trade_observation drop constraint trade_observation_flow_check;
alter table public.trade_observation add constraint trade_observation_flow_check
  check (flow in ('import','export','re_export','re_import'));

-- UN M49 (as Comtrade numbers them; ISO numeric plus Comtrade's own codes) -> ISO alpha-2.
create temporary table m49_iso(code text primary key, iso text not null);
insert into m49_iso values
  ('0','W00'),
  ('4','AF'),
  ('8','AL'),
  ('12','DZ'),
  ('16','AS'),
  ('20','AD'),
  ('24','AO'),
  ('28','AG'),
  ('31','AZ'),
  ('32','AR'),
  ('36','AU'),
  ('40','AT'),
  ('44','BS'),
  ('48','BH'),
  ('50','BD'),
  ('51','AM'),
  ('52','BB'),
  ('56','BE'),
  ('60','BM'),
  ('64','BT'),
  ('68','BO'),
  ('70','BA'),
  ('72','BW'),
  ('76','BR'),
  ('84','BZ'),
  ('90','SB'),
  ('92','VG'),
  ('96','BN'),
  ('100','BG'),
  ('104','MM'),
  ('108','BI'),
  ('112','BY'),
  ('116','KH'),
  ('120','CM'),
  ('124','CA'),
  ('132','CV'),
  ('136','KY'),
  ('140','CF'),
  ('144','LK'),
  ('148','TD'),
  ('152','CL'),
  ('156','CN'),
  ('158','TW'),
  ('170','CO'),
  ('174','KM'),
  ('175','YT'),
  ('178','CG'),
  ('180','CD'),
  ('184','CK'),
  ('188','CR'),
  ('191','HR'),
  ('192','CU'),
  ('196','CY'),
  ('203','CZ'),
  ('204','BJ'),
  ('208','DK'),
  ('212','DM'),
  ('214','DO'),
  ('218','EC'),
  ('222','SV'),
  ('226','GQ'),
  ('231','ET'),
  ('232','ER'),
  ('233','EE'),
  ('234','FO'),
  ('238','FK'),
  ('242','FJ'),
  ('246','FI'),
  ('250','FR'),
  ('251','FR'),
  ('254','GF'),
  ('258','PF'),
  ('262','DJ'),
  ('266','GA'),
  ('268','GE'),
  ('270','GM'),
  ('275','PS'),
  ('276','DE'),
  ('288','GH'),
  ('292','GI'),
  ('296','KI'),
  ('300','GR'),
  ('304','GL'),
  ('308','GD'),
  ('312','GP'),
  ('316','GU'),
  ('320','GT'),
  ('324','GN'),
  ('328','GY'),
  ('332','HT'),
  ('340','HN'),
  ('344','HK'),
  ('348','HU'),
  ('352','IS'),
  ('356','IN'),
  ('360','ID'),
  ('364','IR'),
  ('368','IQ'),
  ('372','IE'),
  ('376','IL'),
  ('380','IT'),
  ('381','IT'),
  ('384','CI'),
  ('388','JM'),
  ('392','JP'),
  ('398','KZ'),
  ('400','JO'),
  ('404','KE'),
  ('408','KP'),
  ('410','KR'),
  ('414','KW'),
  ('417','KG'),
  ('418','LA'),
  ('422','LB'),
  ('426','LS'),
  ('428','LV'),
  ('430','LR'),
  ('434','LY'),
  ('438','LI'),
  ('440','LT'),
  ('442','LU'),
  ('446','MO'),
  ('450','MG'),
  ('454','MW'),
  ('458','MY'),
  ('462','MV'),
  ('466','ML'),
  ('470','MT'),
  ('474','MQ'),
  ('478','MR'),
  ('480','MU'),
  ('484','MX'),
  ('490','TW'),
  ('492','MC'),
  ('496','MN'),
  ('498','MD'),
  ('499','ME'),
  ('500','MS'),
  ('504','MA'),
  ('508','MZ'),
  ('512','OM'),
  ('516','NA'),
  ('520','NR'),
  ('524','NP'),
  ('528','NL'),
  ('531','CW'),
  ('533','AW'),
  ('534','SX'),
  ('540','NC'),
  ('548','VU'),
  ('554','NZ'),
  ('558','NI'),
  ('562','NE'),
  ('566','NG'),
  ('570','NU'),
  ('574','NF'),
  ('578','NO'),
  ('579','NO'),
  ('580','MP'),
  ('583','FM'),
  ('584','MH'),
  ('585','PW'),
  ('586','PK'),
  ('591','PA'),
  ('598','PG'),
  ('600','PY'),
  ('604','PE'),
  ('608','PH'),
  ('612','PN'),
  ('616','PL'),
  ('620','PT'),
  ('624','GW'),
  ('626','TL'),
  ('630','PR'),
  ('634','QA'),
  ('638','RE'),
  ('642','RO'),
  ('643','RU'),
  ('646','RW'),
  ('654','SH'),
  ('659','KN'),
  ('660','AI'),
  ('662','LC'),
  ('666','PM'),
  ('670','VC'),
  ('674','SM'),
  ('678','ST'),
  ('682','SA'),
  ('686','SN'),
  ('688','RS'),
  ('690','SC'),
  ('694','SL'),
  ('699','IN'),
  ('702','SG'),
  ('703','SK'),
  ('704','VN'),
  ('705','SI'),
  ('706','SO'),
  ('710','ZA'),
  ('716','ZW'),
  ('724','ES'),
  ('728','SS'),
  ('729','SD'),
  ('740','SR'),
  ('748','SZ'),
  ('752','SE'),
  ('756','CH'),
  ('757','CH'),
  ('760','SY'),
  ('762','TJ'),
  ('764','TH'),
  ('768','TG'),
  ('776','TO'),
  ('780','TT'),
  ('784','AE'),
  ('788','TN'),
  ('792','TR'),
  ('795','TM'),
  ('796','TC'),
  ('798','TV'),
  ('800','UG'),
  ('804','UA'),
  ('807','MK'),
  ('818','EG'),
  ('826','GB'),
  ('834','TZ'),
  ('840','US'),
  ('842','US'),
  ('854','BF'),
  ('858','UY'),
  ('860','UZ'),
  ('862','VE'),
  ('876','WF'),
  ('882','WS'),
  ('887','YE'),
  ('894','ZM');
update public.trade_observation t set reporter_iso = m.iso
  from m49_iso m where t.reporter ~ '^[0-9]+$' and m.code = ltrim(t.reporter, '0');
update public.trade_observation t set partner_iso = m.iso
  from m49_iso m where t.partner ~ '^[0-9]+$' and m.code = case when t.partner ~ '^0+$' then '0' else ltrim(t.partner, '0') end;
update public.trade_observation set reporter_iso = reporter where reporter_iso is null and reporter ~ '^[A-Z]{2}$';
update public.trade_observation set partner_iso = partner where partner_iso is null and partner ~ '^[A-Z]{2}$';
update public.trade_observation set hs_revision = public.normalize_hs_revision(hs_revision)
  where hs_revision is distinct from public.normalize_hs_revision(hs_revision);
drop table m49_iso;
create index trade_observation_reporter_iso on public.trade_observation(reporter_iso, hs_code, period);

update public.exposure_mapping set hs_revision = public.normalize_hs_revision(hs_revision)
  where hs_revision is distinct from public.normalize_hs_revision(hs_revision);
-- NOT VALID: mappings entered before this migration may lack a revision; new and updated rows
-- must carry one.
alter table public.exposure_mapping add constraint exposure_mapping_hs_revision_required
  check (hs_code is null or hs_revision is not null) not valid;

-- Exposure cap per (issuer, dimension).
create function public.exposure_dimension(geography text, hs_code text) returns text
language sql immutable set search_path='' as $$
  select case
    when geography is not null and hs_code is null then 'geography'
    when hs_code is not null and geography is null then 'hs_product'
    else 'geography_hs_product'
  end
$$;

create or replace function public.check_exposure_total() returns trigger
language plpgsql security invoker set search_path='' as $$
declare
  exceeds_limit boolean;
  new_dimension text := public.exposure_dimension(new.geography, new.hs_code);
begin
  if new.valid_to is not null and new.valid_to < new.valid_from then
    raise exception 'exposure valid_to precedes valid_from';
  end if;
  select exists (
    select 1
      from (
        select new.valid_from as day
        union
        select em.valid_from
          from public.exposure_mapping em
         where em.issuer_id=new.issuer_id
           and public.exposure_dimension(em.geography, em.hs_code) = new_dimension
           and (tg_op <> 'UPDATE' or em.id <> new.id)
           and em.valid_from >= new.valid_from
           and (new.valid_to is null or em.valid_from <= new.valid_to)
           and (em.valid_to is null or em.valid_to >= new.valid_from)
      ) dates
     where (new.valid_to is null or dates.day <= new.valid_to)
       and new.weight + coalesce((
         select sum(em.weight)
           from public.exposure_mapping em
          where em.issuer_id=new.issuer_id
            and public.exposure_dimension(em.geography, em.hs_code) = new_dimension
            and (tg_op <> 'UPDATE' or em.id <> new.id)
            and em.valid_from <= dates.day
            and (em.valid_to is null or em.valid_to >= dates.day)
       ),0) > 1
  ) into exceeds_limit;
  if exceeds_limit then
    raise exception 'mapped % exposure exceeds 100%%', new_dimension;
  end if;
  return new;
end $$;

create table public.hs_concordance (
  from_revision text not null, from_code text not null,
  to_revision text not null, to_code text not null,
  relation text not null check (relation in ('1:1','1:n','n:1','n:n','chapter')),
  note text,
  primary key(from_revision, from_code, to_revision, to_code)
);
-- Chapter numbering and titles are the same in HS2017 and HS2022 (chapter 77 is reserved).
-- Individual subheadings that moved between chapters are NOT tracked at this level; use the
-- WCO correlation table rows (steward.macro.hs.concordance_rows) for exact flows.
insert into public.hs_concordance(from_revision, from_code, to_revision, to_code, relation, note)
select a, lpad(c::text, 2, '0'), b, lpad(c::text, 2, '0'), 'chapter',
       'chapter exists in both revisions; subheading moves not tracked'
  from generate_series(1, 97) c, (values ('HS2017','HS2022'),('HS2022','HS2017')) v(a, b)
 where c <> 77;
alter table public.hs_concordance enable row level security;
revoke all on public.hs_concordance from anon, authenticated;
grant select on public.hs_concordance to authenticated;
grant select, insert, update, delete on public.hs_concordance to app_backend;
create policy owner_read on public.hs_concordance for select to authenticated
  using ((select private.is_owner()));
create policy backend_all on public.hs_concordance for all to app_backend
  using (true) with check (true);

-- ===== 20261005120000_prices_stream_fixes.sql =====
-- Prices stream fixes (audit A1, A15, A16, A17, B21-B24, B26, D3.1):
--
--   price_bar            source CHECK admits every writer (stooq, yahoo, eodhd, alpha_vantage,
--                        twelve_data); Stooq currency/quote_unit derived at write time and
--                        backfilled for existing rows.
--   corporate_action     GBp/ZAc/ILa dividend currencies; stock_dividend needs a ratio (new rows);
--                        explicit owner decision columns (retraction path); (symbol, ex_date) index.
--   exchange_session     index named for what it indexes; `reason` for ad hoc closures.
--   price_return_series  calc_version + inputs_digest: a changed method adds a row instead of
--                        silently overwriting the earlier answer.
--   security_listing / provider_identifier
--                        natural-key unique indexes so the listing writer cannot duplicate rows
--                        through NULL valid_from, and evidence on the listing.
--   refund_provider_credits
--                        gives back credits reserved for a request that never reached the provider.

-- --------------------------------------------------------------------------------------
-- A1: every price writer is admitted by the CHECK
-- --------------------------------------------------------------------------------------
alter table public.price_bar drop constraint if exists price_bar_source_check;
alter table public.price_bar
  add constraint price_bar_source_check
  check (source in ('stooq', 'yahoo', 'eodhd', 'alpha_vantage', 'twelve_data'));

comment on column public.price_bar.source is
  'stooq (source of record), yahoo (unofficial chart API), eodhd and alpha_vantage (gap '
  'fallbacks under free-tier budgets), twelve_data (reserved).';

-- --------------------------------------------------------------------------------------
-- A16 / D3.1: quote currency and unit known at write time
-- --------------------------------------------------------------------------------------
update public.price_bar b
   set currency = m.cur
  from (values ('us', 'USD'), ('uk', 'GBX'), ('de', 'EUR'), ('jp', 'JPY'), ('pl', 'PLN'),
               ('nl', 'EUR'), ('dk', 'DKK'), ('cz', 'CZK')) as m(sfx, cur)
 where b.source = 'stooq' and b.currency is null
   and lower(substring(b.symbol from '\.([A-Za-z]+)$')) = m.sfx;

update public.price_bar
   set quote_unit = case
         when currency in ('GBX', 'GBp') then 'GBX'
         when currency in ('ZAc', 'ZAC') then 'ZAC'
         when currency in ('ILa', 'ILA') then 'ILA'
         else upper(currency) end
 where quote_unit is null and currency is not null;

-- --------------------------------------------------------------------------------------
-- B23 / A15 / B26: corporate actions
-- --------------------------------------------------------------------------------------
alter table public.corporate_action drop constraint if exists corporate_action_currency_check;
alter table public.corporate_action
  add constraint corporate_action_currency_check
  check (currency is null or currency ~ '^[A-Z]{3}$' or currency in ('GBp', 'ZAc', 'ILa'));

-- NOT VALID: existing cash-only stock_dividend rows stay readable (and unusable for returns);
-- new rows must carry a ratio.
alter table public.corporate_action
  add constraint corporate_action_stock_dividend_ratio
  check (action_type <> 'stock_dividend' or ratio is not null) not valid;

alter table public.corporate_action
  add column decided_at timestamptz,
  add column decided_by uuid,
  add column decision_reason text;

comment on column public.corporate_action.decided_at is
  'Set when the owner decided the status explicitly (confirm, reject or retract); a vendor or '
  'collector re-insert never overrides an owner decision.';

create index corporate_action_symbol_ex_date
  on public.corporate_action (symbol, ex_date);

-- --------------------------------------------------------------------------------------
-- B22: exchange_session
-- --------------------------------------------------------------------------------------
-- The earlier name suggested a foreign key to source_evidence; the column is the free-text
-- source label.
alter index if exists public.exchange_session_source_evidence rename to exchange_session_source;

alter table public.exchange_session add column reason text;
comment on column public.exchange_session.reason is
  'Why the day differs from the regular calendar (national holiday observed, ad hoc closure, '
  'early close).';

-- --------------------------------------------------------------------------------------
-- price_return_series versioning
-- --------------------------------------------------------------------------------------
alter table public.price_return_series
  add column calc_version integer not null default 1,
  add column inputs_digest text;

do $$
declare c text;
begin
  select conname into c from pg_constraint
   where conrelid = 'public.price_return_series'::regclass and contype = 'u';
  if c is not null then
    execute format('alter table public.price_return_series drop constraint %I', c);
  end if;
end $$;

create unique index price_return_series_version_key
  on public.price_return_series
  (symbol, start_date, end_date, adjustment_basis, source, calc_version);

comment on column public.price_return_series.calc_version is
  'Version of the return method (split/dividend handling, session calendar). A new method '
  'version stores a new row; the reader takes the newest known_at.';

-- --------------------------------------------------------------------------------------
-- A17: listing writer support
-- --------------------------------------------------------------------------------------
alter table public.security_listing
  add column source text,
  add column evidence jsonb not null default '{}';

-- NULL valid_from made the original unique (security_id, mic, ticker, valid_from) a no-op.
create unique index security_listing_natural_key
  on public.security_listing (security_id, mic, ticker, coalesce(valid_from, date '0001-01-01'));
create unique index provider_identifier_natural_key
  on public.provider_identifier
  (provider, identifier_type, identifier, coalesce(valid_from, date '0001-01-01'));
create index provider_identifier_listing on public.provider_identifier (listing_id)
  where listing_id is not null;

-- --------------------------------------------------------------------------------------
-- B24: give credits back when the request never reached the provider
-- --------------------------------------------------------------------------------------
create or replace function public.refund_provider_credits(
  p_provider text, p_endpoint text, p_cost numeric, p_now timestamptz
) returns void language plpgsql security invoker set search_path = '' as $$
begin
  if p_cost <= 0 then raise exception 'invalid provider credit refund'; end if;
  perform pg_advisory_xact_lock(hashtext(p_provider), hashtext(p_endpoint));
  update public.provider_quota_usage
     set credits = greatest(credits - p_cost, 0), requests = greatest(requests - 1, 0)
   where provider = p_provider and endpoint = p_endpoint
     and ((window_kind = 'minute' and window_start = date_trunc('minute', p_now))
       or (window_kind = 'day' and window_start = date_trunc('day', p_now))
       or (window_kind = 'month' and window_start = date_trunc('month', p_now)));
end $$;

-- ===== 20261005130000_fund_statement_store.sql =====
-- Fundamentals stream fixes (audit A3, A12, A13, B29, B32, F17):
--
-- - fundamentals.source admits 'alphavantage' (the Company Overview fallback inserted it and
--   violated the CHECK, aborting the valuation job for later tickers).
-- - financial_statement_fact becomes a real statement store: text-valued facts (the CIK keeps
--   its leading zeros), canonical line item, filer CIK, acceptance time, amendment lineage.
-- - forward_observation gets a content key so a refresh with the same figures is a no-op (the
--   table duplicated a row on every refresh); existing exact duplicates keep their earliest row.
-- - peer_set records its status history so an owner can un-confirm a peer and an as-of read
--   still sees the status that was in force.
-- - issuer_ir_source (the IR pages to poll) and version columns on issuer_ir_release.
-- - covering indexes for the job_id foreign keys of xbrl_filing and shares_outstanding.

-- ---------------------------------------------------------------------------------------------
-- fundamentals.source
-- ---------------------------------------------------------------------------------------------
do $$ declare c text; begin
  for c in select conname from pg_constraint
           where conrelid = 'public.fundamentals'::regclass and contype = 'c'
             and pg_get_constraintdef(oid) like 'CHECK ((source = ANY%'
  loop
    execute format('alter table public.fundamentals drop constraint %I', c);
  end loop;
end $$;
alter table public.fundamentals add constraint fundamentals_source_check
  check (source in ('sec', 'esef', 'finnhub', 'alphavantage'));

-- ---------------------------------------------------------------------------------------------
-- financial_statement_fact
-- ---------------------------------------------------------------------------------------------
alter table public.financial_statement_fact
  add column cik text check (cik is null or cik ~ '^[0-9]{10}$'),
  add column value_text text,
  add column line_item text,
  add column accepted_at timestamptz,
  add column amends_accession text,
  add column is_amendment boolean not null default false;

do $$ declare c text; begin
  for c in select conname from pg_constraint
           where conrelid = 'public.financial_statement_fact'::regclass and contype = 'c'
             and pg_get_constraintdef(oid) like '%text_block IS NOT NULL%'
  loop
    execute format('alter table public.financial_statement_fact drop constraint %I', c);
  end loop;
end $$;
alter table public.financial_statement_fact add constraint financial_statement_fact_content_check
  check (value is not null or text_block is not null or value_text is not null);

create index financial_fact_filing on public.financial_statement_fact (ticker, accession, source);
create index financial_fact_line_item
  on public.financial_statement_fact (ticker, line_item, period_end desc, known_at desc);

comment on column public.financial_statement_fact.value_text is
  'Non-numeric fact value (dei:EntityCentralIndexKey keeps its leading zeros, document type).';
comment on column public.financial_statement_fact.line_item is
  'Canonical line item (revenue, long_term_debt, lease_liability, ...) of the concept.';
comment on column public.financial_statement_fact.known_at is
  'When the filing became public: the SEC acceptance time (ESEF: the date added). Set once.';

-- ---------------------------------------------------------------------------------------------
-- forward_observation: one row per distinct content
-- ---------------------------------------------------------------------------------------------
alter table public.forward_observation add column observation_key text;

update public.forward_observation set observation_key = md5(concat_ws('|',
  kind, coalesce(provider, ''), metric, fiscal_period, coalesce(basis, ''), coalesce(currency, ''),
  coalesce(vintage, ''), coalesce(period_end::text, ''), coalesce(trim_scale(value)::text, ''),
  coalesce(trim_scale(low)::text, ''), coalesce(trim_scale(high)::text, ''),
  coalesce(analyst_count::text, '')));

delete from public.forward_observation f using public.forward_observation g
  where f.ticker = g.ticker and f.observation_key = g.observation_key and f.id > g.id;

alter table public.forward_observation alter column observation_key set not null;
create unique index forward_observation_content
  on public.forward_observation (ticker, observation_key);

-- Any writer gets the key: a row inserted without one (the backend's own helper passes it
-- computed in Python with the same fields and number formatting) is keyed here.
create function public.forward_observation_set_key() returns trigger
language plpgsql set search_path = '' as $$
begin
  if new.observation_key is null then
    new.observation_key := md5(concat_ws('|',
      new.kind, coalesce(new.provider, ''), new.metric, new.fiscal_period,
      coalesce(new.basis, ''), coalesce(new.currency, ''), coalesce(new.vintage, ''),
      coalesce(new.period_end::text, ''), coalesce(trim_scale(new.value)::text, ''),
      coalesce(trim_scale(new.low)::text, ''), coalesce(trim_scale(new.high)::text, ''),
      coalesce(new.analyst_count::text, '')));
  end if;
  return new;
end $$;
create trigger forward_observation_set_key before insert on public.forward_observation
  for each row execute function public.forward_observation_set_key();

-- ---------------------------------------------------------------------------------------------
-- peer_set: status history
-- ---------------------------------------------------------------------------------------------
alter table public.peer_set add column status_history jsonb not null default '[]'::jsonb;
update public.peer_set
  set status_history = jsonb_build_array(jsonb_build_object('status', status, 'at', known_at));

-- ---------------------------------------------------------------------------------------------
-- IR releases: the pages to poll and the version chain
-- ---------------------------------------------------------------------------------------------
create table public.issuer_ir_source (
  id bigint generated always as identity primary key,
  ticker text not null,
  url text not null check (url ~ '^https://'),
  release_type text not null check (release_type in
    ('guidance', 'consensus', 'segment_consensus', 'capital_return')),
  basis text,
  label text,
  active boolean not null default true,
  last_checked_at timestamptz,
  last_changed_at timestamptz,
  last_hash text,
  last_status text,
  last_error text,
  created_at timestamptz not null default now(),
  unique (ticker, url)
);

alter table public.issuer_ir_release
  add column source_id bigint references public.issuer_ir_source (id) on delete set null,
  add column previous_release_id bigint references public.issuer_ir_release (id)
    on delete set null,
  add column content_type text,
  add column byte_size integer,
  add column fetched_at timestamptz;
create index issuer_ir_release_source on public.issuer_ir_release (source_id);
create index issuer_ir_release_previous on public.issuer_ir_release (previous_release_id);

alter table public.issuer_ir_source enable row level security;
revoke all on public.issuer_ir_source from anon, authenticated;
grant select on public.issuer_ir_source to authenticated;
grant select, insert, update, delete on public.issuer_ir_source to app_backend;
create policy owner_read on public.issuer_ir_source
  for select to authenticated using ((select private.is_owner()));
create policy backend_all on public.issuer_ir_source
  for all to app_backend using (true) with check (true);
grant usage, select on all sequences in schema public to app_backend;

-- ---------------------------------------------------------------------------------------------
-- job_id foreign keys of the SEC XBRL tables
-- ---------------------------------------------------------------------------------------------
create index if not exists xbrl_filing_job on public.xbrl_filing (job_id);
create index if not exists shares_outstanding_job on public.shares_outstanding (job_id);

-- ===== 20261005140000_graph_integrity.sql =====
-- Company graph integrity (stream "graph"): one rejection store across both relationship
-- tables, status history for as-of reads, deal/event decision columns, passage review queue,
-- relationship contradictions, patent publications and dated ownership paths, Wikidata
-- statement provenance, research-observation dedupe and provider auth-failure cooldowns.
--
-- G13 note: 20261001131038_free_source_research_consumers.sql rebuilt source_type_check without
-- 'reference'. 20261002130000_source_type_reference.sql restores it, so a database migrated in
-- order ends with the full list; this migration neither touches that constraint nor depends on
-- it. (On a database that already holds a 'reference' source row, 20261001131038 on its own
-- would fail; fresh databases and the ordered chain do not.)

-- ---------------------------------------------------------------------------------------
-- Normalised object key. Python mirror: steward.graph.keys.normalize_object_key.
--   LEI (20 alphanumerics, last two digits)  -> upper-case LEI
--   wd:Q123                                  -> wd:Q123
--   anything else is a name                  -> name:<casefold, punctuation removed,
--                                               trailing legal-form words removed>
-- ---------------------------------------------------------------------------------------
create or replace function public.graph_norm_object_key(k text) returns text
language plpgsql immutable set search_path = '' as $$
declare s text; prev text; i int := 0;
begin
  if k is null then return null; end if;
  s := btrim(k);
  if s ~ '^[0-9A-Z]{18}[0-9]{2}$' then return s; end if;
  if s ~* '^wd:q[0-9]+$' then return 'wd:' || upper(substr(s, 4)); end if;
  s := regexp_replace(s, '^name:', '', 'i');
  s := lower(s);
  s := replace(s, '&', ' and ');
  s := replace(replace(replace(s, '.', ''), '''', ''), chr(8217), '');
  s := regexp_replace(s, '[^[:alnum:]]+', ' ', 'g');
  s := btrim(regexp_replace(s, '\s+', ' ', 'g'));
  s := regexp_replace(s, '^the ', '');
  loop
    prev := s;
    s := btrim(regexp_replace(s,
      ' (inc|incorporated|corp|corporation|co|company|ltd|limited|plc|ag|sa|nv|se|llc|lp|gmbh|bv|spa|oyj|ab|asa)$',
      ''));
    i := i + 1;
    exit when s = prev or i >= 4;
  end loop;
  return 'name:' || s;
end $$;

-- ultimate_parent is a parent for rejection purposes; the other types are shared verbatim.
create or replace function public.graph_canon_edge_type(t text) returns text
language sql immutable set search_path = '' as $$
  select case when t = 'ultimate_parent' then 'parent' else t end
$$;

create table public.graph_rejection (
  id bigint generated always as identity primary key,
  subject_key text not null,
  object_key_norm text not null,
  edge_type text not null,
  origin text not null,
  decision_reason text,
  decided_at timestamptz not null default now(),
  decided_by uuid,
  unique (subject_key, object_key_norm, edge_type)
);
comment on table public.graph_rejection is
  'Owner rejections keyed on (subject, normalised object, edge type) regardless of source or url, '
  'shared by company_graph_edge and entity_relationship. Reopening deletes the row.';

create table public.graph_status_log (
  id bigint generated always as identity primary key,
  table_name text not null check (table_name in
    ('company_graph_edge','company_event','relationship_disclosure','entity_relationship')),
  row_id bigint not null,
  from_status text,
  to_status text not null,
  reason text,
  changed_at timestamptz not null default now()
);
create index graph_status_log_row on public.graph_status_log (table_name, row_id, changed_at desc);

-- ---------------------------------------------------------------------------------------
-- company_graph_edge: normalised key and the rejection triggers
-- ---------------------------------------------------------------------------------------
alter table public.company_graph_edge add column object_key_norm text;
update public.company_graph_edge set object_key_norm = public.graph_norm_object_key(object_key);
alter table public.company_graph_edge alter column object_key_norm set not null;
create index company_graph_edge_norm
  on public.company_graph_edge (subject_key, object_key_norm, edge_type);

create or replace function public.graph_record_rejection(
  p_subject text, p_norm text, p_type text, p_origin text, p_reason text
) returns void language plpgsql set search_path = '' as $$
begin
  insert into public.graph_rejection (subject_key, object_key_norm, edge_type, origin, decision_reason)
  values (p_subject, p_norm, public.graph_canon_edge_type(p_type), p_origin, p_reason)
  on conflict (subject_key, object_key_norm, edge_type) do nothing;
  -- A rejection reaches every sibling row of either table.
  update public.company_graph_edge
     set status = 'rejected', decided_at = coalesce(decided_at, now()),
         decision_reason = coalesce(decision_reason, 'rejected via ' || p_origin)
   where subject_key = p_subject and object_key_norm = p_norm
     and public.graph_canon_edge_type(edge_type) = public.graph_canon_edge_type(p_type)
     and status <> 'rejected';
  update public.entity_relationship
     set status = 'rejected', decided_at = coalesce(decided_at, now())
   where ticker = p_subject and public.graph_norm_object_key(object_key) = p_norm
     and public.graph_canon_edge_type(type) = public.graph_canon_edge_type(p_type)
     and status <> 'rejected';
end $$;

create or replace function public.graph_edge_before() returns trigger
language plpgsql set search_path = '' as $$
begin
  new.object_key_norm := public.graph_norm_object_key(new.object_key);
  if new.status <> 'rejected' and exists (
      select 1 from public.graph_rejection r
       where r.subject_key = new.subject_key and r.object_key_norm = new.object_key_norm
         and r.edge_type = public.graph_canon_edge_type(new.edge_type)) then
    new.status := 'rejected';
    new.decided_at := coalesce(new.decided_at, now());
    new.decision_reason := coalesce(new.decision_reason, 'previously rejected by the owner');
  end if;
  return new;
end $$;
create trigger company_graph_edge_before before insert or update on public.company_graph_edge
  for each row execute function public.graph_edge_before();

create or replace function public.graph_edge_after() returns trigger
language plpgsql set search_path = '' as $$
begin
  if new.status = 'rejected' and (tg_op = 'INSERT' or old.status is distinct from 'rejected') then
    perform public.graph_record_rejection(new.subject_key, new.object_key_norm, new.edge_type,
                                          'company_graph_edge', new.decision_reason);
  end if;
  return null;
end $$;
create trigger company_graph_edge_after after insert or update of status on public.company_graph_edge
  for each row execute function public.graph_edge_after();

create or replace function public.entity_relationship_before() returns trigger
language plpgsql set search_path = '' as $$
begin
  if new.status <> 'rejected' and exists (
      select 1 from public.graph_rejection r
       where r.subject_key = new.ticker
         and r.object_key_norm = public.graph_norm_object_key(new.object_key)
         and r.edge_type = public.graph_canon_edge_type(new.type)) then
    new.status := 'rejected';
    new.decided_at := coalesce(new.decided_at, now());
  end if;
  return new;
end $$;
create trigger entity_relationship_graph_before before insert or update
  on public.entity_relationship for each row execute function public.entity_relationship_before();

create or replace function public.entity_relationship_after() returns trigger
language plpgsql set search_path = '' as $$
begin
  if new.status = 'rejected' and (tg_op = 'INSERT' or old.status is distinct from 'rejected') then
    perform public.graph_record_rejection(new.ticker, public.graph_norm_object_key(new.object_key),
                                          new.type, 'entity_relationship', null);
  end if;
  return null;
end $$;
create trigger entity_relationship_graph_after after insert or update of status
  on public.entity_relationship for each row execute function public.entity_relationship_after();

-- Existing rejections seed the shared store.
insert into public.graph_rejection (subject_key, object_key_norm, edge_type, origin, decision_reason)
select subject_key, object_key_norm, public.graph_canon_edge_type(edge_type), 'company_graph_edge',
       min(decision_reason)
  from public.company_graph_edge where status = 'rejected'
 group by subject_key, object_key_norm, public.graph_canon_edge_type(edge_type)
on conflict do nothing;
insert into public.graph_rejection (subject_key, object_key_norm, edge_type, origin)
select ticker, public.graph_norm_object_key(object_key), public.graph_canon_edge_type(type),
       'entity_relationship'
  from public.entity_relationship where status = 'rejected'
 group by ticker, public.graph_norm_object_key(object_key), public.graph_canon_edge_type(type)
on conflict do nothing;

-- Generic status history (as-of status reads).
create or replace function public.graph_log_status() returns trigger
language plpgsql set search_path = '' as $$
declare j jsonb := to_jsonb(new);
begin
  if tg_op = 'INSERT' or old.status is distinct from new.status then
    insert into public.graph_status_log (table_name, row_id, from_status, to_status, reason)
    values (tg_table_name, new.id, case when tg_op = 'INSERT' then null else old.status end,
            new.status, j ->> 'decision_reason');
  end if;
  return null;
end $$;
create trigger company_graph_edge_status_log after insert or update of status
  on public.company_graph_edge for each row execute function public.graph_log_status();
create trigger company_event_status_log after insert or update of status
  on public.company_event for each row execute function public.graph_log_status();
create trigger relationship_disclosure_status_log after insert or update of status
  on public.relationship_disclosure for each row execute function public.graph_log_status();

-- Status of a row as the owner knew it at `p_as_of`. Before the first logged change the row
-- had its initial status; with no cutoff the latest status is returned.
create or replace function public.graph_status_as_of(p_table text, p_id bigint, p_as_of timestamptz)
returns text language sql stable set search_path = '' as $$
  select coalesce(
    (select to_status from public.graph_status_log
      where table_name = p_table and row_id = p_id and (p_as_of is null or changed_at <= p_as_of)
      order by changed_at desc, id desc limit 1),
    (select to_status from public.graph_status_log
      where table_name = p_table and row_id = p_id order by changed_at, id limit 1))
$$;
grant execute on function public.graph_status_as_of(text,bigint,timestamptz) to app_backend;

-- Backfill: one baseline entry per existing row so as-of reads have a starting point.
insert into public.graph_status_log (table_name, row_id, from_status, to_status, reason, changed_at)
select 'company_graph_edge', id, null, status, decision_reason, known_at from public.company_graph_edge;
insert into public.graph_status_log (table_name, row_id, from_status, to_status, changed_at)
select 'company_event', id, null, status, known_at from public.company_event;

-- ---------------------------------------------------------------------------------------
-- company_event: decisions, evidence tier, deal grouping
-- ---------------------------------------------------------------------------------------
alter table public.company_event
  add column decided_at timestamptz,
  add column decision_reason text,
  add column evidence_tier text,
  add column deal_key text;
update public.company_event set decided_at = known_at where status in ('confirmed','rejected');
create index company_event_deal on public.company_event (ticker, deal_key, known_at)
  where deal_key is not null;
-- Existing confirmed rows without evidence stay readable; new ones must carry evidence.
alter table public.company_event add constraint company_event_confirmed_needs_evidence
  check (status <> 'confirmed' or evidence_id is not null) not valid;

alter table public.company_graph_edge
  add column decided_by uuid;

-- ---------------------------------------------------------------------------------------
-- relationship_disclosure: unique key, normalised counterparty, contradictions, review queue
-- ---------------------------------------------------------------------------------------
alter table public.relationship_disclosure
  add column counterparty_key_norm text,
  add column updated_at timestamptz not null default now(),
  add column origin text not null default 'owner_review';
update public.relationship_disclosure
   set counterparty_key_norm = public.graph_norm_object_key(coalesce(counterparty_key, counterparty_name))
 where not anonymous_counterparty;

create or replace function public.relationship_disclosure_before() returns trigger
language plpgsql set search_path = '' as $$
begin
  new.counterparty_key_norm := case when new.anonymous_counterparty then null
    else public.graph_norm_object_key(coalesce(new.counterparty_key, new.counterparty_name)) end;
  new.updated_at := now();
  return new;
end $$;
create trigger relationship_disclosure_before before insert or update
  on public.relationship_disclosure for each row execute function public.relationship_disclosure_before();

-- Keep the latest row of each duplicate group (decided rows win over undecided ones).
delete from public.relationship_disclosure d using (
  select id, row_number() over (
    partition by ticker, relationship, direction,
      coalesce(counterparty_key_norm, 'anon:' || md5(passage)), coalesce(fiscal_period, ''), source_url
    order by (status in ('confirmed','rejected','disputed')) desc, id desc) rn
  from public.relationship_disclosure) x
 where d.id = x.id and x.rn > 1;
create unique index relationship_disclosure_unique on public.relationship_disclosure
  (ticker, relationship, direction, coalesce(counterparty_key_norm, 'anon:' || md5(passage)),
   coalesce(fiscal_period, ''), source_url);

insert into public.graph_status_log (table_name, row_id, from_status, to_status, reason, changed_at)
select 'relationship_disclosure', id, null, status, decision_reason, known_at
  from public.relationship_disclosure;

create table public.relationship_contradiction (
  id bigint generated always as identity primary key,
  ticker text not null,
  relationship text not null,
  counterparty_key_norm text,
  fiscal_period text,
  kind text not null check (kind in ('direction_conflict','share_conflict','named_vs_anonymous',
    'share_total_exceeds_one','period_overlap')),
  disclosure_id bigint not null references public.relationship_disclosure(id) on delete cascade,
  other_disclosure_id bigint references public.relationship_disclosure(id) on delete cascade,
  detail jsonb not null default '{}',
  status text not null default 'open' check (status in ('open','resolved','dismissed')),
  resolution text,
  created_at timestamptz not null default now(),
  resolved_at timestamptz
);
create unique index relationship_contradiction_unique on public.relationship_contradiction
  (disclosure_id, coalesce(other_disclosure_id, 0), kind);
create index relationship_contradiction_other on public.relationship_contradiction (other_disclosure_id);
create index relationship_contradiction_open on public.relationship_contradiction (ticker, status);

-- Filing-hit candidates: the keyword shortcut no longer asserts a role.
alter table public.filing_hit drop constraint if exists filing_hit_kind_check;
alter table public.filing_hit add constraint filing_hit_kind_check
  check (kind in ('topic','customer','supplier','relationship_candidate'));
update public.filing_hit set kind = 'relationship_candidate' where kind in ('customer','supplier');
alter table public.filing_hit
  add column review_state text not null default 'pending'
    check (review_state in ('pending','fetched','resolved','dismissed','failed')),
  add column passage text,
  add column passage_hash text,
  add column fetched_at timestamptz,
  add column review_note text,
  add column attempts integer not null default 0,
  add column disclosure_id bigint references public.relationship_disclosure(id) on delete set null,
  add column updated_at timestamptz not null default now();
update public.filing_hit set review_state = 'pending' where kind = 'relationship_candidate';
create index filing_hit_review on public.filing_hit (review_state, filed_at desc)
  where kind = 'relationship_candidate';
create index filing_hit_disclosure on public.filing_hit (disclosure_id);

-- ---------------------------------------------------------------------------------------
-- Patents: OPS family id, publications, grant/expiry, dated ownership path edge
-- ---------------------------------------------------------------------------------------
alter table public.patent_family
  add column family_id_source text not null default 'publication'
    check (family_id_source in ('ops_family_id','publication','owner')),
  add column grant_date date,
  add column expiry_date date,
  add column legal_events jsonb not null default '[]',
  add column first_seen_status text;
alter table public.patent_family_owner
  add column path_edge_id bigint references public.company_graph_edge(id);
create index patent_family_owner_path_edge on public.patent_family_owner (path_edge_id);
create index patent_family_owner_ticker on public.patent_family_owner (ticker, family_id);

create table public.patent_publication (
  id bigint generated always as identity primary key,
  family_id bigint not null references public.patent_family(id) on delete cascade,
  country text not null,
  doc_number text not null,
  kind_code text not null,
  publication_date date,
  unique (country, doc_number, kind_code)
);
create index patent_publication_family on public.patent_publication (family_id);

-- ---------------------------------------------------------------------------------------
-- Wikidata statements with provenance, rank, last check and superseded retention
-- ---------------------------------------------------------------------------------------
create table public.wikidata_statement (
  id bigint generated always as identity primary key,
  qid text not null check (qid ~ '^Q[0-9]+$'),
  property_id text not null check (property_id ~ '^P[0-9]+$'),
  statement_id text not null,
  value_qid text,
  value_text text,
  rank text not null check (rank in ('preferred','normal','deprecated')),
  qualifiers jsonb not null default '{}',
  statement_references jsonb not null default '[]',
  start_time date,
  end_time date,
  content_hash text not null,
  first_seen_at timestamptz not null default now(),
  last_checked_at timestamptz not null default now(),
  superseded_at timestamptz,
  superseded_by_id bigint references public.wikidata_statement(id),
  unique (statement_id, content_hash)
);
create index wikidata_statement_current on public.wikidata_statement (qid, property_id)
  where superseded_at is null;
create index wikidata_statement_superseded_by on public.wikidata_statement (superseded_by_id);

-- ---------------------------------------------------------------------------------------
-- D5.10 reconciliation of parent / successor changes against deal events
-- ---------------------------------------------------------------------------------------
create table public.graph_reconciliation (
  id bigint generated always as identity primary key,
  ticker text not null,
  change_kind text not null check (change_kind in ('parent_changed','successor_named','parent_removed')),
  from_key text,
  to_key text,
  observed_source text not null,
  observed_at timestamptz not null default now(),
  outcome text not null check (outcome in ('explained_by_closed_deal','deal_pending',
    'deal_terminated_or_reversed','unexplained')),
  deal_event_id bigint references public.company_event(id),
  detail jsonb not null default '{}',
  status text not null default 'open' check (status in ('open','accepted','dismissed'))
);
create unique index graph_reconciliation_unique on public.graph_reconciliation
  (ticker, change_kind, coalesce(from_key, ''), coalesce(to_key, ''), observed_source);
create index graph_reconciliation_deal on public.graph_reconciliation (deal_event_id);

-- ---------------------------------------------------------------------------------------
-- company_research_observation: dedupe key (identical re-fetches no longer duplicate)
-- ---------------------------------------------------------------------------------------
delete from public.company_research_observation d using (
  select id, row_number() over (
    partition by ticker, provider, dataset, metric, coalesce(period_end, date '0001-01-01'),
      coalesce(trim_scale(value)::text, ''), coalesce(text_value, ''), coalesce(unit, '')
    order by known_at, id) rn
  from public.company_research_observation) x
 where d.id = x.id and x.rn > 1;
create unique index company_research_observation_unique on public.company_research_observation
  (ticker, provider, dataset, metric, coalesce(period_end, date '0001-01-01'),
   coalesce(trim_scale(value)::text, ''), coalesce(text_value, ''), coalesce(unit, ''));
alter table public.company_research_observation
  add column last_seen_at timestamptz not null default now();

-- Per-ticker lookup cooldown for paid-credit enrichment (PDL) and similar.
create table public.research_lookup_log (
  provider text not null,
  lookup_key text not null,
  looked_up_at timestamptz not null default now(),
  outcome text not null check (outcome in ('matched','no_match','failed','refunded')),
  primary key (provider, lookup_key)
);

-- Auth-failure cooldown: a transient 403 must not hard-block an endpoint forever.
create table public.provider_auth_failure (
  provider text not null,
  endpoint text not null,
  consecutive integer not null default 0,
  last_status integer,
  last_failure_at timestamptz,
  blocked_until timestamptz,
  permanent boolean not null default false,
  primary key (provider, endpoint)
);

grant execute on function public.graph_norm_object_key(text) to app_backend;
grant execute on function public.graph_canon_edge_type(text) to app_backend;

-- ---------------------------------------------------------------------------------------
-- Access model for the new tables
-- ---------------------------------------------------------------------------------------
do $$ declare t text; begin foreach t in array array[
 'graph_rejection','graph_status_log','relationship_contradiction','patent_publication',
 'wikidata_statement','graph_reconciliation','research_lookup_log','provider_auth_failure'] loop
 execute format('alter table public.%I enable row level security',t);
 execute format('revoke all on public.%I from anon,authenticated',t);
 execute format('grant select on public.%I to authenticated',t);
 execute format('grant select,insert,update,delete on public.%I to app_backend',t);
 execute format('create policy owner_read on public.%I for select to authenticated using ((select private.is_owner()))',t);
 execute format('create policy backend_all on public.%I for all to app_backend using (true) with check (true)',t);
end loop; end $$;
grant usage,select on all sequences in schema public to app_backend;

-- TED contract award notices: public, keyless; the first successful call verifies the endpoint.
insert into public.provider_access(provider,endpoint,state,credential_env,quota,quota_period,detail)
values ('ted','notice_search','access_unverified',null,null,null,
        'public keyless search API; the first successful call verifies it')
on conflict(provider,endpoint) do nothing;
insert into public.provider_endpoint_manifest(provider,endpoint,path_template,response_unit,production_consumer)
values ('ted','notice_search','/v3/notices/search','award notices','procurement award disclosures')
on conflict(provider,endpoint) do nothing;

-- ===== 20261005150000_funds_stream_hardening.sql =====
-- Funds / ownership hardening (audit B34, B39, C-lows, UK PDMR persistence).
--
-- - insider_filing_ticker      filing -> held ticker link: several tickers share one issuer CIK
--                              (dual-class), but insider_filing keeps one `ticker` per accession.
-- - fund_factsheet             insert-only versions (a re-fetch with different figures adds a row
--                              instead of rewriting history; point-in-time reads use fetched_at)
--                              and the snapshot FK no longer cascades: pruning a holdings snapshot
--                              keeps the factsheet history.
-- - control_stake_skipped_filing  13D/G accessions already downloaded and skipped (not held,
--                              unstructured, unparseable) so they are not re-downloaded each run.
-- - nport_ignored_filing       N-PORT filings of a registrant's other series (not downloaded again).
-- - pdmr_notification / pdmr_transaction  parsed UK MAR Article 19 notifications and the
--                              honest outcome of every retrieval attempt.

-- ---------------------------------------------------------------------------------------
-- B34 filing -> ticker link
-- ---------------------------------------------------------------------------------------
create table public.insider_filing_ticker (
  accession text not null references public.insider_filing (accession) on delete cascade,
  ticker    text not null,
  linked_at timestamptz not null default now(),
  primary key (accession, ticker)
);
create index insider_filing_ticker_ticker on public.insider_filing_ticker (ticker);

insert into public.insider_filing_ticker (accession, ticker)
select accession, ticker from public.insider_filing
on conflict do nothing;
-- Every other held ticker that shares the filing's issuer CIK (dual-class listings).
insert into public.insider_filing_ticker (accession, ticker)
select f.accession, i.t212_ticker
from public.insider_filing f
join public.instrument i on i.cik is not null and lpad(i.cik::text, 10, '0') = f.issuer_cik
on conflict do nothing;

-- ---------------------------------------------------------------------------------------
-- B39 / C-low fund_factsheet: insert-only versions, history survives snapshot pruning
-- ---------------------------------------------------------------------------------------
alter table public.fund_factsheet
  drop constraint fund_factsheet_fund_isin_as_of_source_key;
create index fund_factsheet_version
  on public.fund_factsheet (fund_isin, as_of, source, fetched_at desc, id desc);
create index fund_factsheet_known
  on public.fund_factsheet (fund_isin, fetched_at desc, id desc);
alter table public.fund_factsheet
  drop constraint fund_factsheet_snapshot_id_fkey,
  add constraint fund_factsheet_snapshot_id_fkey
    foreign key (snapshot_id) references public.etf_snapshot (id) on delete set null;

-- ---------------------------------------------------------------------------------------
-- C-low control stakes: accessions already looked at and skipped
-- ---------------------------------------------------------------------------------------
create table public.control_stake_skipped_filing (
  accession       text primary key check (accession ~ '^[0-9]{10}-[0-9]{2}-[0-9]{6}$'),
  form            text not null,
  filed           date not null,
  reason          text not null check (reason in ('unstructured', 'not_held', 'parse_failed')),
  detail          text,
  issuer_cik      text check (issuer_cik is null or issuer_cik ~ '^[0-9]{10}$'),
  issuer_cusip    text check (issuer_cusip is null or issuer_cusip ~ '^[0-9A-Z]{9}$'),
  attempts        integer not null default 1,
  first_seen_at   timestamptz not null default now(),
  last_checked_at timestamptz not null default now()
);
create index control_stake_skipped_filing_reason
  on public.control_stake_skipped_filing (reason, last_checked_at);

-- ---------------------------------------------------------------------------------------
-- B40 N-PORT filings of a registrant's other series, seen and ignored
-- ---------------------------------------------------------------------------------------
create table public.nport_ignored_filing (
  accession         text primary key,
  registrant_cik    text,
  series_id         text,
  report_period_end date,
  filed             date,
  noted_at          timestamptz not null default now()
);

-- ---------------------------------------------------------------------------------------
-- UK PDMR notifications (MAR Article 19) from the RNS announcement stream
-- ---------------------------------------------------------------------------------------
create table public.pdmr_notification (
  id              bigint generated always as identity primary key,
  source_url      text not null unique,
  news_item_id    bigint references public.news_item (id) on delete set null,
  ticker          text,
  headline        text,
  announced_at    timestamptz,
  -- parsed: the template was read; the others record why nothing was read.
  status          text not null check (status in (
                    'parsed', 'no_template', 'access_unverified', 'paid_only',
                    'quota_exhausted', 'temporarily_failed')),
  detail          text,
  attempts        integer not null default 1,
  person_name     text,
  position        text,
  is_amendment    boolean,
  issuer_name     text,
  lei             text,
  first_attempt_at timestamptz not null default now(),
  last_attempt_at  timestamptz not null default now(),
  parsed_at       timestamptz
);
create index pdmr_notification_news_item on public.pdmr_notification (news_item_id)
  where news_item_id is not null;
create index pdmr_notification_ticker on public.pdmr_notification (ticker, announced_at desc);
create index pdmr_notification_status on public.pdmr_notification (status, last_attempt_at);

create table public.pdmr_transaction (
  notification_id bigint not null references public.pdmr_notification (id) on delete cascade,
  row_index       integer not null check (row_index >= 0),
  instrument      text,
  isin            text check (isin is null or isin ~ '^[A-Z]{2}[A-Z0-9]{9}[0-9]$'),
  nature          text,
  price           numeric,
  currency        text,
  volume          numeric,
  transaction_date date,
  venue           text,
  economic_class  text not null check (economic_class in (
                    'open_market_buy', 'open_market_sell', 'option_exercise', 'grant_award',
                    'gift', 'tax_withholding', 'conversion', 'other')),
  classification_basis text not null,
  primary key (notification_id, row_index)
);

alter table public.insider_filing_ticker enable row level security;
alter table public.control_stake_skipped_filing enable row level security;
alter table public.pdmr_notification enable row level security;
alter table public.pdmr_transaction enable row level security;
alter table public.nport_ignored_filing enable row level security;
revoke all on public.insider_filing_ticker, public.control_stake_skipped_filing,
  public.pdmr_notification, public.pdmr_transaction, public.nport_ignored_filing
  from anon, authenticated;
grant select on public.insider_filing_ticker, public.control_stake_skipped_filing,
  public.pdmr_notification, public.pdmr_transaction, public.nport_ignored_filing
  to authenticated;
create policy owner_read on public.nport_ignored_filing
  for select to authenticated using ((select private.is_owner()));
create policy owner_read on public.insider_filing_ticker
  for select to authenticated using ((select private.is_owner()));
create policy owner_read on public.control_stake_skipped_filing
  for select to authenticated using ((select private.is_owner()));
create policy owner_read on public.pdmr_notification
  for select to authenticated using ((select private.is_owner()));
create policy owner_read on public.pdmr_transaction
  for select to authenticated using ((select private.is_owner()));
grant select, insert, update, delete on public.insider_filing_ticker,
  public.control_stake_skipped_filing, public.pdmr_notification,
  public.pdmr_transaction, public.nport_ignored_filing to app_backend;
create policy backend_all on public.nport_ignored_filing
  for all to app_backend using (true) with check (true);
create policy backend_all on public.insider_filing_ticker
  for all to app_backend using (true) with check (true);
create policy backend_all on public.control_stake_skipped_filing
  for all to app_backend using (true) with check (true);
create policy backend_all on public.pdmr_notification
  for all to app_backend using (true) with check (true);
create policy backend_all on public.pdmr_transaction
  for all to app_backend using (true) with check (true);

comment on table public.insider_filing_ticker is
  'Which held tickers a Form 3/4/5 applies to: several tickers can share one issuer CIK.';
comment on table public.control_stake_skipped_filing is
  'Schedule 13D/G accessions downloaded once and skipped (not held, unstructured, unparseable).';
comment on table public.pdmr_notification is
  'UK PDMR announcements: the parsed notification, or the honest reason nothing was read.';

-- ===== 20261005160000_free_source_alert_gates.sql =====
-- Phase 8 gate enforcement for the free-source alerts: a decision the verification gates
-- blocked is recorded in the trail as `blocked_by_gate`. Such a row is provisional: the
-- evaluator replaces it by the real outcome once the gate opens (it never counts as "seen").
-- Rebuilt from the live constraint: every earlier value is kept.

alter table public.free_source_alert_trail drop constraint free_source_alert_trail_outcome_check;
alter table public.free_source_alert_trail add constraint free_source_alert_trail_outcome_check
  check (outcome in ('raised', 'throttled', 'revision_ignored', 'baseline', 'not_material',
    'no_baseline', 'persisting', 'review_needed', 'below_severity', 'run_cap',
    'blocked_by_gate'));

-- ===== 20261005170000_verification_gate_hardening.sql =====
-- Phase 8 gate hardening (audit B47, B51).
--
-- reconciliation_sample: one sample per (domain, subject, metric, period) - 30 copies of one
--   value no longer satisfy min_samples - a sample cannot be dated in the future, and every
--   sample names the person who read it (reviewer) and where it was read (source and url).
-- signal_audit_result: an audit cannot be dated in the future and names its reviewer.
-- provider_access: the one credentialed fmp endpoint without a declared free quota gets the
--   same 250/day the other fmp endpoints carry, so the zero-cost audit can prove a hard stop.
--
-- The new CHECK constraints are NOT VALID: rows recorded before this migration are left as they
-- are (nothing is rewritten or invented), but the gates (thresholds.domain_gate and
-- signal_audit.publish_gate) ignore any row that would violate them, so legacy rows never count
-- as evidence.  Duplicate reconciliation samples are collapsed to the newest one.

delete from public.reconciliation_sample a
 using public.reconciliation_sample b
 where a.domain = b.domain and a.subject_key = b.subject_key and a.metric = b.metric
   and a.period_end is not distinct from b.period_end
   and (a.sampled_at, a.id) < (b.sampled_at, b.id);

create unique index reconciliation_sample_unique_key
  on public.reconciliation_sample (domain, subject_key, metric, (coalesce(period_end, date '0001-01-01')));

alter table public.reconciliation_sample
  add constraint reconciliation_sample_not_future
    check (sampled_at <= now() + interval '5 minutes') not valid,
  add constraint reconciliation_sample_reviewer_named
    check (reviewer is not null and btrim(reviewer) <> '') not valid,
  add constraint reconciliation_sample_source_named
    check (btrim(primary_source) <> '' and primary_url is not null and btrim(primary_url) <> '') not valid;

alter table public.signal_audit_result
  add constraint signal_audit_result_not_future
    check (audited_at <= now() + interval '5 minutes') not valid,
  add constraint signal_audit_result_reviewer_named
    check (reviewer is not null and btrim(reviewer) <> '') not valid;

update public.provider_access
   set quota = 250, quota_period = 'day'
 where provider = 'fmp' and endpoint = 'sector_snapshot' and quota is null;

commit;
