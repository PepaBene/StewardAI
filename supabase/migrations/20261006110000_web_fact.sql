-- Gap research: facts the structured sources left empty, found on reliable web pages and
-- approved (or not) by an AI approver. They are kept apart from the structured tables: panels
-- read them only as a labelled fallback where the structured slot is empty, and a structured
-- value arriving later supersedes them. The owner can reject or re-approve any row.
create table public.web_fact (
  id           bigint generated always as identity primary key,
  ticker       text not null,
  slot         text not null,
  value_text   text not null,
  value_num    numeric,
  value_date   date,
  unit         text,
  period       text,
  source_url   text not null check (source_url ~ '^https://'),
  host         text not null,
  tier         text not null check (tier in ('T1', 'T2', 'T3', 'rejected')),
  quote        text not null,
  retrieved_at timestamptz not null default now(),
  status       text not null check (status in ('approved', 'needs_review', 'rejected', 'superseded')),
  decided_by   text not null,
  confidence   numeric check (confidence between 0 and 1),
  reasons      text,
  origin       text not null default 'web_search',
  status_history jsonb not null default '[]'::jsonb,
  job_id       bigint references public.job (id),
  unique (ticker, slot, source_url, value_text)
);
create index web_fact_ticker_slot on public.web_fact (ticker, slot, status);

-- One row per search attempt, so a slot nothing can fill stops costing money (cooldown) and
-- a slot that cannot be filled from the web at all is recorded as unavailable.
create table public.web_fact_attempt (
  id           bigint generated always as identity primary key,
  ticker       text not null,
  slot         text not null,
  attempted_at timestamptz not null default now(),
  outcome      text not null check (outcome in ('approved', 'needs_review', 'none', 'error')),
  detail       jsonb not null default '{}'::jsonb
);
create index web_fact_attempt_slot on public.web_fact_attempt (ticker, slot, attempted_at desc);

alter table public.web_fact enable row level security;
alter table public.web_fact_attempt enable row level security;
grant select, insert, update, delete on public.web_fact, public.web_fact_attempt to app_backend;
grant select on public.web_fact, public.web_fact_attempt to authenticated;
create policy backend_all on public.web_fact for all to app_backend using (true) with check (true);
create policy owner_read on public.web_fact for select to authenticated
  using ((select private.is_owner()));
create policy backend_all on public.web_fact_attempt for all to app_backend
  using (true) with check (true);
create policy owner_read on public.web_fact_attempt for select to authenticated
  using ((select private.is_owner()));
