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
