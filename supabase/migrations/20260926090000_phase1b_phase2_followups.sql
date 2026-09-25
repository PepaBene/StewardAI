-- Phase 1b and the Phase 2 follow-ups.
--
--   job_step           + cache_write_tokens, cache_read_tokens, batch and the prices the step
--                      was costed with (from the Budget config), so every cost_usd can be
--                      recomputed by hand from its own row.
--   fact               + origin: 'extraction' (read from a document by the pipeline) or
--                      'owner' (a correction the owner entered; it restates the fact it
--                      corrects). Owner overrides always win.
--   fact_review        every owner decision on a held or quarantined fact (accept, correct,
--                      reject), with its reason. Append-only.
--   fact_reconciliation cross-source checks of stored facts against the issuer's later
--                      10-Q/10-K XBRL figures. A mismatch quarantines the fact.
--
-- Same access model as every other table: RLS on, owner-only SELECT for the frontend,
-- `anon` gets nothing, `app_backend` gets explicit grants plus a permissive policy.

-- ---------------------------------------------------------------------------
-- Cost accounting
-- ---------------------------------------------------------------------------
alter table public.job_step
  add column cache_write_tokens integer,
  add column cache_read_tokens  integer,
  add column batch              boolean,
  add column prices             jsonb;

comment on column public.job_step.input_tokens is
  'Uncached input tokens. Cache writes and reads are in their own columns.';
comment on column public.job_step.prices is
  'Budget config prices the step was costed with: cost_usd = tokens x these prices.';

create index job_step_cost on public.job_step (started_at) where cost_usd is not null;

-- ---------------------------------------------------------------------------
-- Owner review and overrides
-- ---------------------------------------------------------------------------
alter table public.fact
  add column origin text not null default 'extraction' check (origin in ('extraction', 'owner'));

comment on column public.fact.origin is
  'extraction: read from the archived document by the pipeline. owner: a value the owner '
  'entered to correct the fact in restates_fact_id; it wins over the extracted one.';

create table public.fact_review (
  id                bigint generated always as identity primary key,
  fact_id           bigint      not null references public.fact (id),
  action            text        not null check (action in ('accept', 'correct', 'reject')),
  old_status        text        not null,
  new_status        text        not null,
  reason            text        not null check (length(trim(reason)) > 0),
  -- For a correction: the new fact holding the owner's value.
  corrected_fact_id bigint      references public.fact (id),
  reviewed_by       uuid,
  job_id            bigint      references public.job (id),
  created_at        timestamptz not null default now()
);

comment on table public.fact_review is
  'Append-only log of owner decisions on held or quarantined facts.';

create index fact_review_fact on public.fact_review (fact_id, id desc);

-- ---------------------------------------------------------------------------
-- Cross-source reconciliation
-- ---------------------------------------------------------------------------
create table public.fact_reconciliation (
  id            bigint generated always as identity primary key,
  fact_id       bigint      not null references public.fact (id),
  source        text        not null check (source in ('xbrl')),
  -- The XBRL concept, filing and period the fact was compared with.
  concept       text,
  accession     text,
  form          text,
  period_start  date,
  period_end    date,
  -- "reported" (a duration fact as filed) or "derived_q4" (full year minus nine months).
  derivation    text        check (derivation in ('reported', 'derived_q4')),
  xbrl_value    numeric,
  fact_value    numeric,
  difference    numeric,
  outcome       text        not null check (outcome in ('match', 'mismatch', 'no_data')),
  detail        text,
  job_id        bigint      references public.job (id),
  checked_at    timestamptz not null default now()
);

comment on table public.fact_reconciliation is
  'Each comparison of a stored fact with the issuer''s XBRL figure. Append-only.';

create index fact_reconciliation_fact on public.fact_reconciliation (fact_id, id desc);

-- ---------------------------------------------------------------------------
-- Transactions view: filters by type and period
-- ---------------------------------------------------------------------------
create index transaction_type_occurred_at on public.transaction (type, occurred_at desc);

-- ---------------------------------------------------------------------------
-- Privileges and row-level security
-- ---------------------------------------------------------------------------
alter table public.fact_review enable row level security;
alter table public.fact_reconciliation enable row level security;

revoke all on public.fact_review, public.fact_reconciliation from anon, authenticated;
grant select on public.fact_review, public.fact_reconciliation to authenticated;

create policy owner_read on public.fact_review
  for select to authenticated using ((select private.is_owner()));
create policy owner_read on public.fact_reconciliation
  for select to authenticated using ((select private.is_owner()));

grant select, insert on public.fact_review, public.fact_reconciliation to app_backend;

create policy backend_all on public.fact_review
  for all to app_backend using (true) with check (true);
create policy backend_all on public.fact_reconciliation
  for all to app_backend using (true) with check (true);
