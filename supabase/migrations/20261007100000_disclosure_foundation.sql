-- Public-disclosure plan P1.1: the shared filing / evidence / observation / claim / review schema.
--
-- Additive on top of source_document, source_document_version, source_evidence, issuer,
-- security, identity_match_decision and issuer_relationship: those rows stay the truth for
-- documents, evidence and corporate succession. New tables reference them instead of copying.
-- Domain tables for later phases (ownership, deals, estimates) land with their own phases.
--
-- Every as-of read uses `known_at <= as_of`; `event_effective_at` is kept apart so a
-- backdated disclosure cannot leak into a historical replay.

-- ---------------------------------------------------------------------------------------
-- disclosure_source: one row per endpoint / form / issuer page. A source may be enabled only
-- after a recorded free-access test (`access_state = 'available'`); marketing claims enable nothing.
-- ---------------------------------------------------------------------------------------
create table public.disclosure_source (
  id bigint generated always as identity primary key,
  source_key text not null unique check (source_key ~ '^[a-z0-9][a-z0-9_.-]{1,62}$'),
  provider text not null,
  endpoint text not null,
  jurisdiction text not null check (jurisdiction ~ '^[A-Z]{2}$|^EU$|^XX$'),
  regulator text not null,
  access_method text not null check (access_method in
    ('none','api_key','registration','oauth','browser_session')),
  cost text not null default 'free' check (cost = 'free'),
  rate_limit_per_second numeric check (rate_limit_per_second is null or rate_limit_per_second > 0),
  requires_user_agent boolean not null default false,
  document_formats text[] not null default '{}',
  publication_lag interval,
  stop_condition text not null,
  access_state text not null default 'untested' check (access_state in
    ('untested','available','authentication_failed','retrieval_failed','paid_only','not_covered')),
  tested_on date,
  access_detail text,
  enabled boolean not null default false,
  cursor jsonb not null default '{}',
  last_checked_at timestamptz,
  created_at timestamptz not null default now(),
  check (not enabled or access_state = 'available')
);

-- ---------------------------------------------------------------------------------------
-- Documents: who it is about, which form and period, which source found it, first sighting.
-- ---------------------------------------------------------------------------------------
alter table public.source_document
  add column issuer_id bigint references public.issuer(id),
  add column form text,
  add column fiscal_period text,
  add column language text check (language is null or language ~ '^[a-z]{2,3}$'),
  add column source_key text references public.disclosure_source(source_key),
  add column first_seen_at timestamptz not null default now();
create index source_document_issuer on public.source_document(issuer_id, form);
create index source_document_source_key on public.source_document(source_key);

-- Insert-time facts about a version. `date_notes` explains every date left null
-- ({"accepted_at": "the regulator publishes no acceptance time"}).
alter table public.source_document_version
  add column first_seen_at timestamptz not null default now(),
  add column byte_length bigint check (byte_length is null or byte_length >= 0),
  add column archive_path text,
  add column date_notes jsonb not null default '{}',
  add column quarantined boolean not null default false,
  add column quarantine_reason text,
  add column parser_version text,
  add check (not quarantined or quarantine_reason is not null);
create index source_document_version_known on public.source_document_version(known_at);

-- Evidence span detail. source_evidence stays append-only; new columns are insert-time.
alter table public.source_evidence
  add column section text,
  add column row_ref text,
  add column extraction_method text,
  add column parser_version text,
  add column confidence numeric check (confidence is null or confidence between 0 and 1);

-- ---------------------------------------------------------------------------------------
-- disclosure_observation: a typed metric value read from a cited span. Null is distinct from
-- zero and from "not disclosed": `value_state` says which one it is.
-- ---------------------------------------------------------------------------------------
create table public.disclosure_observation (
  id bigint generated always as identity primary key,
  metric text not null,
  issuer_id bigint not null references public.issuer(id),
  security_id bigint references public.security(id),
  segment text,
  period_start date,
  period_end date not null,
  is_instant boolean not null default false,
  value_state text not null default 'reported' check (value_state in
    ('reported','not_disclosed','null')),
  value numeric,
  text_value text,
  original_string text,
  unit text not null,
  scale integer not null default 0,
  currency text check (currency is null or currency ~ '^[A-Z]{3}$'),
  basis text,
  dimensions jsonb not null default '{}',
  document_version_id bigint not null references public.source_document_version(id),
  evidence_id bigint not null references public.source_evidence(id),
  source_observation_id bigint references public.source_observation(id),
  source_precedence integer not null default 100,
  parser_version text not null,
  valid_from timestamptz,
  valid_to timestamptz,
  event_effective_at timestamptz,
  known_at timestamptz not null,
  supersedes_id bigint references public.disclosure_observation(id),
  review_state text not null default 'machine' check (review_state in
    ('machine','under_review','verified','rejected','superseded')),
  created_at timestamptz not null default now(),
  check (period_start is null or period_end >= period_start),
  check ((value_state = 'reported') = (value is not null or text_value is not null))
);
create unique index disclosure_observation_identity on public.disclosure_observation
  (document_version_id, metric, coalesce(segment, ''), period_end, md5(dimensions::text),
   parser_version);
create index disclosure_observation_asof on public.disclosure_observation
  (issuer_id, known_at);
create index disclosure_observation_metric on public.disclosure_observation
  (issuer_id, metric, period_end desc);
create index disclosure_observation_evidence on public.disclosure_observation(evidence_id);
create index disclosure_observation_supersedes on public.disclosure_observation(supersedes_id);
create index disclosure_observation_security on public.disclosure_observation(security_id);
create index disclosure_observation_source_obs
  on public.disclosure_observation(source_observation_id);

-- ---------------------------------------------------------------------------------------
-- extracted_claim: a typed assertion about a subject (and optionally an object) with its
-- evidence. A machine suggestion is never a verified fact: `verified` needs a decider.
-- An anonymous counterparty ("Customer A") has a label and no entity id.
-- ---------------------------------------------------------------------------------------
create table public.extracted_claim (
  id bigint generated always as identity primary key,
  claim_type text not null,
  subject_issuer_id bigint references public.issuer(id),
  subject_label text,
  object_issuer_id bigint references public.issuer(id),
  object_label text,
  object_anonymous boolean not null default false,
  direction text check (direction is null or direction in
    ('supplier_to_customer','customer_from_supplier','owner_of','owned_by','none')),
  polarity text not null default 'asserted' check (polarity in
    ('asserted','denied','superseded')),
  payload jsonb not null default '{}',
  evidence_id bigint not null references public.source_evidence(id),
  document_version_id bigint not null references public.source_document_version(id),
  review_state text not null default 'machine_suggested' check (review_state in
    ('machine_suggested','under_review','verified','rejected','superseded')),
  confidence numeric check (confidence is null or confidence between 0 and 1),
  method_version text not null,
  valid_from date,
  valid_to date,
  event_effective_at timestamptz,
  known_at timestamptz not null,
  supersedes_claim_id bigint references public.extracted_claim(id),
  legacy_table text,
  legacy_key text,
  decided_by text,
  decided_at timestamptz,
  created_at timestamptz not null default now(),
  check (valid_to is null or valid_from is null or valid_to >= valid_from),
  check (subject_issuer_id is not null or subject_label is not null),
  check (object_issuer_id is null or not object_anonymous),
  check (review_state not in ('verified','rejected') or (decided_by is not null and decided_at is not null))
);
create index extracted_claim_edge on public.extracted_claim
  (subject_issuer_id, object_issuer_id, valid_from);
create index extracted_claim_asof on public.extracted_claim(subject_issuer_id, known_at);
create index extracted_claim_object on public.extracted_claim(object_issuer_id);
create index extracted_claim_evidence on public.extracted_claim(evidence_id);
create index extracted_claim_version on public.extracted_claim(document_version_id);
create index extracted_claim_supersedes on public.extracted_claim(supersedes_claim_id);
create unique index extracted_claim_legacy on public.extracted_claim(legacy_table, legacy_key)
  where legacy_table is not null;

-- ---------------------------------------------------------------------------------------
-- disclosure_coverage: the per-entity state machine the UI must distinguish.
-- ---------------------------------------------------------------------------------------
create table public.disclosure_coverage (
  id bigint generated always as identity primary key,
  dataset text not null,
  source_key text references public.disclosure_source(source_key),
  jurisdiction text,
  issuer_id bigint references public.issuer(id),
  entity_key text not null default '',
  metric text not null default '',
  period text not null default '',
  state text not null check (state in
    ('not_disclosed','not_covered','paid_only','authentication_failed','retrieval_failed',
     'parse_failed','unresolved_identity','under_review','verified','stale')),
  reason text,
  checked_at timestamptz not null default now(),
  last_verified_observation_id bigint references public.disclosure_observation(id)
);
create unique index disclosure_coverage_key on public.disclosure_coverage
  (dataset, coalesce(source_key, ''), coalesce(issuer_id, 0), entity_key, metric, period);
create index disclosure_coverage_issuer on public.disclosure_coverage(issuer_id);
create index disclosure_coverage_source on public.disclosure_coverage(source_key);
create index disclosure_coverage_obs on public.disclosure_coverage(last_verified_observation_id);

-- ---------------------------------------------------------------------------------------
-- quality_review + audit trail. The queue keeps the original candidate; a decision records who,
-- why and when. A rejected or superseded subject is excluded from authoritative reads.
-- ---------------------------------------------------------------------------------------
create table public.quality_review (
  id bigint generated always as identity primary key,
  subject_type text not null check (subject_type in
    ('claim','observation','document_version','identity_mapping')),
  subject_id bigint not null,
  reason text not null check (reason in
    ('low_confidence','entity_ambiguity','contradiction','format_change','parse_failure')),
  status text not null default 'open' check (status in
    ('open','approved','rejected','superseded','deferred')),
  candidate jsonb not null,
  assignee text,
  decision_reason text,
  decided_by text,
  decided_at timestamptz,
  created_at timestamptz not null default now(),
  check (status in ('open','deferred') or (decided_by is not null and decided_at is not null
    and decision_reason is not null and length(btrim(decision_reason)) > 0))
);
create unique index quality_review_open on public.quality_review(subject_type, subject_id, reason)
  where status in ('open','deferred');
create index quality_review_queue on public.quality_review(status, created_at);

create table public.quality_review_event (
  id bigint generated always as identity primary key,
  review_id bigint not null references public.quality_review(id),
  at timestamptz not null default now(),
  actor text not null,
  action text not null,
  detail jsonb not null default '{}'
);
create index quality_review_event_review on public.quality_review_event(review_id, at);

-- ---------------------------------------------------------------------------------------
-- Identity: share class, a reviewed mapping from a document mention to an entity, and
-- anonymous counterparties that never receive an invented company id.
-- ---------------------------------------------------------------------------------------
alter table public.security add column share_class text;

create table public.disclosure_entity_mapping (
  id bigint generated always as identity primary key,
  document_version_id bigint references public.source_document_version(id),
  mention text not null,
  mention_kind text not null check (mention_kind in ('issuer','security','person','counterparty')),
  issuer_id bigint references public.issuer(id),
  security_id bigint references public.security(id),
  anonymous_label text,
  status text not null default 'suggested' check (status in
    ('suggested','confirmed','rejected','unresolved')),
  score numeric check (score is null or score between 0 and 1),
  candidate_ids jsonb not null default '[]',
  match_basis text not null default 'name' check (match_basis in
    ('cik','lei','isin','figi','registered_name','alias','name')),
  rejection_reason text,
  decided_by text,
  decided_at timestamptz,
  known_at timestamptz not null default now(),
  check (anonymous_label is null or (issuer_id is null and security_id is null)),
  check (status <> 'confirmed' or issuer_id is not null or security_id is not null
         or anonymous_label is not null),
  -- a name-only match is never confirmed without a decider.
  check (status <> 'confirmed' or match_basis <> 'name' or decided_by is not null),
  check (status <> 'rejected' or rejection_reason is not null)
);
create index disclosure_entity_mapping_version on public.disclosure_entity_mapping(document_version_id);
create index disclosure_entity_mapping_issuer on public.disclosure_entity_mapping(issuer_id);
create index disclosure_entity_mapping_security on public.disclosure_entity_mapping(security_id);
create unique index disclosure_entity_mapping_mention on public.disclosure_entity_mapping
  (coalesce(document_version_id, 0), lower(mention), mention_kind);

-- ---------------------------------------------------------------------------------------
-- Append-only guards: observations and claims are corrected by superseding rows, never edited
-- in place, except for review bookkeeping.
-- ---------------------------------------------------------------------------------------
-- Ending or correcting a row inserts a successor (`supersedes_id` / `supersedes_claim_id`) with its
-- own `known_at`; mutating valid_to or polarity would leak later knowledge into an as-of replay.
create trigger append_only before update or delete on public.disclosure_observation
  for each row execute function public.forbid_append_only_mutation('review_state');
create trigger append_only before update or delete on public.extracted_claim
  for each row execute function public.forbid_append_only_mutation(
    'review_state','decided_by','decided_at');
create trigger append_only before update or delete on public.quality_review_event
  for each row execute function public.forbid_append_only_mutation();

-- ---------------------------------------------------------------------------------------
-- Owner-only reads, backend writes.
-- ---------------------------------------------------------------------------------------
do $$
declare t text;
begin
  foreach t in array array['disclosure_source','disclosure_observation','extracted_claim',
      'disclosure_coverage','quality_review','quality_review_event','disclosure_entity_mapping']
  loop
    execute format('alter table public.%I enable row level security', t);
    execute format('revoke all on public.%I from anon, authenticated', t);
    execute format('grant select on public.%I to authenticated', t);
    execute format('grant select, insert, update, delete on public.%I to app_backend', t);
    execute format('create policy owner_read on public.%I for select to authenticated
      using ((select private.is_owner()))', t);
    execute format('create policy backend_all on public.%I for all to app_backend
      using (true) with check (true)', t);
  end loop;
end $$;
