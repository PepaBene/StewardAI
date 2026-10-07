-- Plan P7: published consensus, named estimates, guidance and segment estimates as versioned,
-- point-in-time snapshots.
--
-- consensus_source is the per-issuer registry (P7.1). A missing file or an incompatible
-- definition is a COVERAGE GAP (`access_state` + `gap_reason`), never a licensing block: the
-- access vocabulary is the one `disclosure_source.access_state` uses.
--
-- public_estimate_snapshot is one dated file (or one named broker note, or one owner scenario).
-- Five series are never mixed: company_guidance, published_analyst_consensus,
-- named_individual_estimate, owner_scenario, event_calendar_estimate. The series of a snapshot
-- cannot change after insert (append-only trigger; only `corrected_by_id` may be filled in), a
-- management target cannot carry an analyst count, a mean or a median, and a named estimate needs
-- its analyst and broker. A corrected IR file is a NEW snapshot with supersedes_id; the old row
-- stays so an earlier surprise is still scored with what was known then.
--
-- estimate_value is one metric/scope/fiscal-period line of a snapshot. mean, median, high and low
-- are each nullable: null is "not stated", never zero. Additive only.

create table public.consensus_source (
  id bigint generated always as identity primary key,
  issuer_id bigint not null references public.issuer(id),
  source_key text not null unique check (source_key ~ '^[a-z0-9][a-z0-9_.-]{1,62}$'),
  url text not null,
  discovery_method text not null,
  publisher text not null,
  origin text not null default 'unknown' check (origin in
    ('third_party_brokers', 'issuer_compiled', 'unknown')),
  format text not null check (format in ('html', 'pdf', 'xlsx')),
  authentication text not null default 'none' check (authentication in
    ('none', 'registration', 'login')),
  cadence text,
  sample_files jsonb not null default '[]',
  selected_metrics_only boolean not null default false,
  selected_brokers_only boolean not null default false,
  access_state text not null default 'untested' check (access_state in
    ('untested', 'available', 'authentication_failed', 'retrieval_failed', 'paid_only',
     'not_covered')),
  tested_on date,
  gap_reason text,
  enabled boolean not null default false,
  created_at timestamptz not null default now(),
  check (access_state in ('untested', 'available') or gap_reason is not null),
  check (not enabled or access_state = 'available')
);
create index consensus_source_issuer on public.consensus_source (issuer_id);

create table public.public_estimate_snapshot (
  id bigint generated always as identity primary key,
  issuer_id bigint not null references public.issuer(id),
  source_id bigint references public.consensus_source(id),
  document_version_id bigint references public.source_document_version(id),
  content_hash text,
  series text not null check (series in (
    'company_guidance', 'published_analyst_consensus', 'named_individual_estimate',
    'owner_scenario', 'event_calendar_estimate')),
  published_at timestamptz not null,
  known_at timestamptz not null,
  panel_description text,
  analyst_count integer check (analyst_count is null or analyst_count >= 0),
  panel_method_version text,
  analyst_name text,
  broker_name text,
  supersedes_id bigint references public.public_estimate_snapshot(id),
  corrected_by_id bigint references public.public_estimate_snapshot(id),
  created_at timestamptz not null default now(),
  check (series <> 'company_guidance' or (analyst_count is null and panel_description is null)),
  check (series <> 'named_individual_estimate'
         or (coalesce(btrim(analyst_name), '') <> '' and coalesce(btrim(broker_name), '') <> '')),
  check (series in ('named_individual_estimate') or (analyst_name is null and broker_name is null)),
  check (series in ('owner_scenario') or source_id is not null),
  check (supersedes_id is null or supersedes_id <> id)
);
create unique index public_estimate_snapshot_version
  on public.public_estimate_snapshot (source_id, content_hash) where content_hash is not null;
create index public_estimate_snapshot_asof
  on public.public_estimate_snapshot (issuer_id, series, known_at);
create index public_estimate_snapshot_source on public.public_estimate_snapshot (source_id);
create index public_estimate_snapshot_supersedes on public.public_estimate_snapshot (supersedes_id);
create index public_estimate_snapshot_docversion
  on public.public_estimate_snapshot (document_version_id);
create trigger append_only before update or delete on public.public_estimate_snapshot
  for each row execute function public.forbid_append_only_mutation('corrected_by_id');

create table public.estimate_value (
  id bigint generated always as identity primary key,
  snapshot_id bigint not null references public.public_estimate_snapshot(id),
  metric text not null check (length(btrim(metric)) > 0),
  segment text,
  segment_taxonomy text,
  non_additive boolean not null default false,
  restated_prior boolean not null default false,
  fiscal_period text not null,
  fiscal_calendar text,
  period_start date,
  period_end date not null,
  currency text check (currency is null or currency ~ '^[A-Z]{3}$'),
  unit text not null check (unit in ('amount', 'per_share', 'percent', 'count')),
  scale integer not null default 0,
  basis text not null check (basis in ('GAAP', 'IFRS', 'adjusted')),
  basis_definition text,
  mean numeric,
  median numeric,
  high numeric,
  low numeric,
  analyst_count integer check (analyst_count is null or analyst_count >= 0),
  evidence_id bigint references public.source_evidence(id),
  original_string text,
  created_at timestamptz not null default now(),
  check (period_start is null or period_end >= period_start),
  check (low is null or high is null or low <= high),
  check (mean is null or ((low is null or low <= mean) and (high is null or mean <= high))),
  check (median is null or ((low is null or low <= median) and (high is null or median <= high))),
  check (mean is not null or median is not null or high is not null or low is not null)
);
create unique index estimate_value_identity on public.estimate_value
  (snapshot_id, metric, coalesce(segment, ''), period_end, basis);
create index estimate_value_lookup
  on public.estimate_value (snapshot_id, metric, coalesce(segment, ''), fiscal_period);
create index estimate_value_evidence on public.estimate_value (evidence_id);
create trigger append_only before update or delete on public.estimate_value
  for each row execute function public.forbid_append_only_mutation();

-- A management target is a bracket (low/high) and never an analyst view.
create function public.estimate_value_series_guard() returns trigger
language plpgsql set search_path = '' as $$
declare s text;
begin
  select series into s from public.public_estimate_snapshot where id = new.snapshot_id;
  if s = 'company_guidance' and (new.mean is not null or new.median is not null
                                 or new.analyst_count is not null) then
    raise exception 'company guidance is a management target: low/high only, never a mean, median or analyst count'
      using errcode = 'check_violation';
  end if;
  return new;
end $$;
create trigger estimate_value_series_guard before insert on public.estimate_value
  for each row execute function public.estimate_value_series_guard();

-- Review of consecutive-snapshot pairs: the revision-alert gate counts these (latest label wins).
create table public.estimate_pair_review (
  id bigint generated always as identity primary key,
  earlier_value_id bigint not null references public.estimate_value(id),
  later_value_id bigint not null references public.estimate_value(id),
  correct boolean not null,
  reviewer text not null check (length(btrim(reviewer)) > 0),
  note text,
  decided_at timestamptz not null default now(),
  check (earlier_value_id <> later_value_id)
);
create index estimate_pair_review_pair
  on public.estimate_pair_review (earlier_value_id, later_value_id, id desc);
create index estimate_pair_review_later on public.estimate_pair_review (later_value_id);
create trigger append_only before update or delete on public.estimate_pair_review
  for each row execute function public.forbid_append_only_mutation();

-- RLS: owner read, backend service role writes.
do $$
declare t text;
begin
  foreach t in array array['consensus_source', 'public_estimate_snapshot', 'estimate_value',
                           'estimate_pair_review'] loop
    execute format('alter table public.%I enable row level security', t);
    execute format('revoke all on public.%I from anon, authenticated', t);
    execute format('grant select on public.%I to authenticated', t);
    execute format(
      'create policy owner_read on public.%I for select to authenticated using ((select private.is_owner()))', t);
    execute format('grant select, insert, update, delete on public.%I to app_backend', t);
    execute format(
      'create policy backend_all on public.%I for all to app_backend using (true) with check (true)', t);
  end loop;
end $$;
