-- Phase 2: extraction and earnings checks.
--
--   job.params           parameters of a run (e.g. {"ticker": "AAPL_US_EQ", "report_date": "..."}).
--   instrument           + last_report_date, report_date_checked_at for the earnings calendar.
--   evidence             one row per archived source document (page or PDF), keyed by the
--                        SHA-256 of its bytes; the bytes live in the Storage bucket `evidence`.
--   fact                 one extracted figure with its source URL, verbatim snippet, archived
--                        evidence and the validator's outcome. Never overwritten: a new value
--                        for a stored period is a new row that points at the one it restates.
--   verdict              pass / breach / unknown per thesis KPI and period, tied to the theses
--                        config version it was judged under and the facts it used.
--
-- Same access model as every other table: RLS on, owner-only SELECT for the frontend,
-- `anon` gets nothing, `app_backend` gets explicit grants plus a permissive policy.

-- ---------------------------------------------------------------------------
-- Job parameters and calendar bookkeeping
-- ---------------------------------------------------------------------------
alter table public.job
  add column params jsonb not null default '{}'::jsonb
    constraint job_params_is_object check (jsonb_typeof(params) = 'object');

create index job_type_ticker on public.job (job_type, (params ->> 'ticker'), created_at desc);

alter table public.instrument
  add column last_report_date      date,
  add column report_date_checked_at timestamptz;

comment on column public.instrument.last_report_date is
  'Report date of the last earnings release a successful earnings check read.';

-- ---------------------------------------------------------------------------
-- evidence: archived source documents
-- ---------------------------------------------------------------------------
create table public.evidence (
  sha256        text        primary key check (sha256 ~ '^[0-9a-f]{64}$'),
  storage_path  text        not null unique,
  source_url    text        not null,
  content_type  text        not null,
  byte_size     integer     not null check (byte_size > 0),
  title         text,
  fetched_at    timestamptz not null default now(),
  job_id        bigint      references public.job (id)
);

comment on table public.evidence is
  'Every page or PDF a stored fact was extracted from. Bytes are in Storage bucket "evidence" at storage_path.';

-- ---------------------------------------------------------------------------
-- fact: extracted, validated figures
-- ---------------------------------------------------------------------------
create table public.fact (
  id                 bigint generated always as identity primary key,
  ticker             text        not null,
  metric             text        not null check (metric ~ '^[a-z][a-z0-9_]{0,63}$'),
  basis              text        not null check (basis in ('reported', 'adjusted')),
  fiscal_year        integer     not null check (fiscal_year between 1900 and 2200),
  fiscal_period      text        not null check (fiscal_period in ('Q1', 'Q2', 'Q3', 'Q4', 'H1', 'H2', 'FY')),
  -- "FY2025 Q3", "FY2025": the company's own fiscal label.
  period             text        not null,
  -- Calendar end of the fiscal period, from the holding profile's fiscal year end.
  period_end         date,
  -- The figure in the document's stated scale, and the number exactly as printed.
  value              numeric     not null,
  value_as_written   text        not null,
  scale              text        not null check (scale in ('units', 'thousands', 'millions', 'billions')),
  value_units        numeric     generated always as (
                       value * case scale
                         when 'thousands' then 1e3
                         when 'millions'  then 1e6
                         when 'billions'  then 1e9
                         else 1 end
                     ) stored,
  unit               text        not null check (unit in ('currency', 'per_share', 'percent', 'count', 'ratio')),
  currency           text        check (currency ~ '^[A-Z]{3}$'),
  source_url         text        not null,
  snippet            text        not null,
  unit_snippet       text,
  evidence_sha256    text        not null references public.evidence (sha256),
  evidence_path      text        not null,
  -- verified: every check passed; unverified: held for owner review (sanity bound,
  -- restatement); rejected: failed a hard check; quarantined: marked wrong by the owner.
  status             text        not null check (status in ('verified', 'unverified', 'rejected', 'quarantined')),
  status_reason      text,
  -- One entry per validator check: {"check", "ok", "detail"}.
  checks             jsonb       not null default '[]'::jsonb,
  restates_fact_id   bigint      references public.fact (id),
  job_id             bigint      references public.job (id),
  config_version_ids bigint[]    not null default '{}',
  extracted_at       timestamptz not null default now(),
  reviewed_at        timestamptz,
  reviewed_by        uuid
);

comment on column public.fact.value_units is 'value with the scale applied (e.g. 94036 millions -> 94036000000).';
comment on column public.fact.restates_fact_id is
  'The earlier fact for the same ticker, metric, basis and period whose value this one differs from.';

create index fact_lookup
  on public.fact (ticker, metric, basis, fiscal_year, fiscal_period, id desc);
create index fact_evidence on public.fact (evidence_sha256);

-- A fact's figure and provenance never change; only its review state does. (value_units is
-- generated, and a BEFORE trigger sees it before it is computed, so it is left out.)
create function private.fact_guard()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if tg_op = 'DELETE' then
    raise exception 'fact rows cannot be deleted; quarantine them instead';
  end if;
  if (to_jsonb(new) - array['status', 'status_reason', 'reviewed_at', 'reviewed_by', 'value_units'])
     is distinct from
     (to_jsonb(old) - array['status', 'status_reason', 'reviewed_at', 'reviewed_by', 'value_units']) then
    raise exception 'fact rows are immutable; only status, status_reason and review fields may change';
  end if;
  return new;
end;
$$;

create trigger fact_immutable_figures
  before update or delete on public.fact
  for each row execute function private.fact_guard();

-- ---------------------------------------------------------------------------
-- verdict: one judgement per thesis KPI and fiscal period
-- ---------------------------------------------------------------------------
create table public.verdict (
  id                bigint generated always as identity primary key,
  ticker            text        not null,
  kpi_id            text        not null,
  metric            text        not null,
  basis             text        not null check (basis in ('reported', 'adjusted')),
  kpi_period        text        not null check (kpi_period in ('quarter', 'annual', 'ttm')),
  -- The fiscal period judged; null when no verified figure exists for any period.
  fiscal_year       integer,
  fiscal_period     text        check (fiscal_period in ('Q1', 'Q2', 'Q3', 'Q4', 'H1', 'H2', 'FY')),
  period            text,
  status            text        not null check (status in ('pass', 'breach', 'unknown')),
  -- Observed value in the KPI's own unit (e.g. 9.63 for "%"), computed in code.
  observed_value    numeric,
  operator          text        not null check (operator in ('<', '<=', '>', '>=', '==')),
  threshold         numeric     not null,
  threshold_unit    text        not null,
  rationale         text        not null,
  fact_ids          bigint[]    not null default '{}',
  config_version_id bigint      not null references public.config_version (id),
  job_id            bigint      references public.job (id),
  created_at        timestamptz not null default now()
);

comment on table public.verdict is
  'Append-only. The latest row per (ticker, kpi_id, fiscal period) is the current verdict; older rows are its history.';

create index verdict_lookup on public.verdict (ticker, kpi_id, fiscal_year, fiscal_period, id desc);

-- ---------------------------------------------------------------------------
-- Privileges and row-level security
-- ---------------------------------------------------------------------------
alter table public.evidence enable row level security;
alter table public.fact enable row level security;
alter table public.verdict enable row level security;

revoke all on public.evidence, public.fact, public.verdict from anon, authenticated;
grant select on public.evidence, public.fact, public.verdict to authenticated;

create policy owner_read on public.evidence
  for select to authenticated using ((select private.is_owner()));
create policy owner_read on public.fact
  for select to authenticated using ((select private.is_owner()));
create policy owner_read on public.verdict
  for select to authenticated using ((select private.is_owner()));

-- Backend: evidence and verdicts are append-only; facts change review state only.
grant select, insert on public.evidence, public.verdict to app_backend;
grant select, insert, update on public.fact to app_backend;

create policy backend_all on public.evidence
  for all to app_backend using (true) with check (true);
create policy backend_all on public.fact
  for all to app_backend using (true) with check (true);
create policy backend_all on public.verdict
  for all to app_backend using (true) with check (true);

-- ---------------------------------------------------------------------------
-- Storage: the private `evidence` bucket. The backend writes to it through the Storage
-- REST API with the project's secret key; the frontend gets short-lived signed URLs from
-- the API. (Supabase only; a plain Postgres has no storage schema.)
-- ---------------------------------------------------------------------------
do $$
begin
  if to_regclass('storage.buckets') is not null then
    insert into storage.buckets (id, name, public, file_size_limit)
    values ('evidence', 'evidence', false, 52428800)
    on conflict (id) do nothing;
  end if;
end
$$;
