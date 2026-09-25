-- Phase 1: portfolio core.
--
--   job / job_step       every job run and each of its steps (status, timings, cost fields
--                        for the AI jobs of later phases). The frontend follows them live
--                        through Supabase Realtime.
--   fx_rate              ČNB and ECB reference rates.
--   account_snapshot     one row per day: cash and totals as Trading212 reports them, plus
--                        the same figures converted to the strategy's base currency.
--   position_snapshot    one row per position per day.
--   transaction          orders (fills), dividends and cash movements from Trading212.
--   sync_state           small key/value store for collector progress (history backfill).
--
-- Same access model as the foundation tables: RLS on, the owner may SELECT from the
-- frontend, `anon` gets nothing, `app_backend` gets explicit grants plus a permissive policy.

-- ---------------------------------------------------------------------------
-- Jobs
-- ---------------------------------------------------------------------------
create table public.job (
  id                 bigint generated always as identity primary key,
  job_type           text        not null check (job_type ~ '^[a-z][a-z0-9_]{0,63}$'),
  trigger            text        not null check (trigger in ('schedule', 'manual', 'event')),
  status             text        not null default 'queued'
                                 check (status in ('queued', 'waiting', 'running', 'succeeded', 'failed')),
  -- Active config versions the run read, one per domain it used.
  config_version_ids bigint[]    not null default '{}',
  requested_by       uuid,
  error              text,
  created_at         timestamptz not null default now(),
  started_at         timestamptz,
  finished_at        timestamptz
);

comment on table public.job is
  'One row per job run. status: queued (enqueued), waiting (for the job-type lock), running, succeeded, failed.';

create index job_type_created_at on public.job (job_type, created_at desc);

create table public.job_step (
  id            bigint generated always as identity primary key,
  job_id        bigint      not null references public.job (id) on delete cascade,
  step_index    integer     not null check (step_index >= 0),
  name          text        not null,
  status        text        not null check (status in ('running', 'succeeded', 'failed')),
  started_at    timestamptz not null default now(),
  finished_at   timestamptz,
  -- Filled by the AI jobs of later phases; null for code-only steps.
  model         text,
  input_tokens  integer,
  output_tokens integer,
  searches      integer,
  cost_usd      numeric(12, 6),
  detail        jsonb,
  error         text,
  unique (job_id, step_index)
);

comment on table public.job_step is
  'Each step of a job run. A holding with no successful run in its window becomes visible here.';

-- ---------------------------------------------------------------------------
-- FX reference rates
-- ---------------------------------------------------------------------------
create table public.fx_rate (
  source           text        not null check (source in ('cnb', 'ecb')),
  rate_date        date        not null,
  currency         text        not null check (currency ~ '^[A-Z]{3}$'),
  home_currency    text        not null check (home_currency ~ '^[A-Z]{3}$'),
  -- Units of home_currency for ONE unit of currency (ČNB: CZK, ECB: EUR).
  rate             numeric     not null check (rate > 0),
  -- The figure exactly as published: ČNB gives CZK per `published_amount` units
  -- (e.g. per 100 JPY); ECB gives units of `currency` per 1 EUR.
  published_amount numeric     not null check (published_amount > 0),
  published_rate   numeric     not null check (published_rate > 0),
  fetched_at       timestamptz not null default now(),
  primary key (source, rate_date, currency)
);

comment on table public.fx_rate is 'ČNB daily fixing and ECB reference rates, as published and normalised.';

-- ---------------------------------------------------------------------------
-- Snapshots
-- ---------------------------------------------------------------------------
create table public.account_snapshot (
  snapshot_date     date        primary key,
  job_id            bigint      not null references public.job (id),
  captured_at       timestamptz not null,
  config_version_id bigint      references public.config_version (id),
  t212_account_id   bigint      not null,
  account_currency  text        not null check (account_currency ~ '^[A-Z]{3}$'),
  -- As Trading212 reports them, in the account currency.
  total_value       numeric     not null,
  cash_available    numeric     not null,
  cash_in_pies      numeric     not null,
  cash_reserved     numeric     not null,
  invested_value    numeric     not null,
  invested_cost     numeric     not null,
  unrealized_ppl    numeric     not null,
  realized_ppl      numeric     not null,
  -- Converted with reference rates to the strategy's base currency.
  base_currency     text        not null check (base_currency ~ '^[A-Z]{3}$'),
  fx_source         text        not null check (fx_source in ('cnb', 'ecb')),
  fx_rate           numeric     not null check (fx_rate > 0),
  fx_rate_date      date,
  cash_base         numeric     not null,
  invested_base     numeric     not null,
  total_base        numeric     not null,
  weight_basis      text        not null check (weight_basis in ('total', 'invested'))
);

comment on column public.account_snapshot.fx_rate is
  'Base-currency units per account-currency unit; 1 when they are the same currency.';

create table public.position_snapshot (
  snapshot_date       date        not null,
  ticker              text        not null references public.instrument (t212_ticker),
  job_id              bigint      not null references public.job (id),
  captured_at         timestamptz not null,
  config_version_id   bigint      references public.config_version (id),
  quantity            numeric     not null,
  avg_price           numeric     not null,
  current_price       numeric     not null,
  -- Instrument currency as Trading212 reports it; may be a minor unit such as GBX.
  instrument_currency text        not null,
  -- quantity * current_price, in the instrument currency.
  value               numeric     not null,
  -- Trading212's own conversion to the account currency.
  account_currency    text        not null,
  value_account       numeric     not null,
  cost_account        numeric     not null,
  ppl                 numeric     not null,
  fx_impact_account   numeric,
  -- Reference-rate conversion to the strategy's base currency.
  base_currency       text        not null,
  fx_source           text        not null check (fx_source in ('cnb', 'ecb')),
  fx_rate             numeric     not null check (fx_rate > 0),
  fx_rate_date        date,
  value_base          numeric     not null,
  weight              numeric,
  primary key (snapshot_date, ticker)
);

comment on column public.position_snapshot.fx_rate is
  'Base-currency units per ONE unit of instrument_currency (minor units already scaled: GBX = GBP/100).';
comment on column public.position_snapshot.weight is
  'value_base as a fraction of the weight basis (account_snapshot.weight_basis) at sync time.';

create index position_snapshot_ticker_date on public.position_snapshot (ticker, snapshot_date desc);

-- ---------------------------------------------------------------------------
-- Transactions (orders, dividends, cash movements)
-- ---------------------------------------------------------------------------
create table public.transaction (
  -- 'fill:<Trading212 fill id>', 'dividend:<reference>' or 'cash:<reference>'.
  id          text        primary key,
  type        text        not null check (type in ('order', 'dividend', 'cash')),
  -- BUY / SELL, the dividend type, or DEPOSIT / WITHDRAW / FEE / ...
  subtype     text,
  ticker      text,
  quantity    numeric,
  price       numeric,
  -- Amount in `currency` as Trading212 reports it (the account currency for fills and dividends).
  amount      numeric     not null,
  currency    text,
  occurred_at timestamptz not null,
  raw         jsonb       not null,
  job_id      bigint      references public.job (id),
  created_at  timestamptz not null default now()
);

create index transaction_occurred_at on public.transaction (occurred_at desc);
create index transaction_ticker on public.transaction (ticker, occurred_at desc);

create table public.sync_state (
  key        text        primary key,
  value      jsonb       not null,
  updated_at timestamptz not null default now()
);

create trigger sync_state_set_updated_at
  before update on public.sync_state
  for each row execute function private.set_updated_at();

-- ---------------------------------------------------------------------------
-- Privileges and row-level security
-- ---------------------------------------------------------------------------
alter table public.job enable row level security;
alter table public.job_step enable row level security;
alter table public.fx_rate enable row level security;
alter table public.account_snapshot enable row level security;
alter table public.position_snapshot enable row level security;
alter table public.transaction enable row level security;
alter table public.sync_state enable row level security;

revoke all on public.job, public.job_step, public.fx_rate, public.account_snapshot,
  public.position_snapshot, public.transaction, public.sync_state
  from anon, authenticated;

-- Frontend: owner-only reads (sync_state is backend bookkeeping and stays hidden).
grant select on public.job, public.job_step, public.fx_rate, public.account_snapshot,
  public.position_snapshot, public.transaction
  to authenticated;

create policy owner_read on public.job
  for select to authenticated using ((select private.is_owner()));
create policy owner_read on public.job_step
  for select to authenticated using ((select private.is_owner()));
create policy owner_read on public.fx_rate
  for select to authenticated using ((select private.is_owner()));
create policy owner_read on public.account_snapshot
  for select to authenticated using ((select private.is_owner()));
create policy owner_read on public.position_snapshot
  for select to authenticated using ((select private.is_owner()));
create policy owner_read on public.transaction
  for select to authenticated using ((select private.is_owner()));

-- Backend. Job history and transactions are append/update only; snapshots of a day are
-- replaced as a whole when that day is re-synced.
grant select, insert, update on public.job, public.job_step to app_backend;
grant select, insert, update on public.fx_rate to app_backend;
grant select, insert, update, delete on public.account_snapshot, public.position_snapshot
  to app_backend;
grant select, insert, update on public.transaction to app_backend;
grant select, insert, update on public.sync_state to app_backend;

create policy backend_all on public.job
  for all to app_backend using (true) with check (true);
create policy backend_all on public.job_step
  for all to app_backend using (true) with check (true);
create policy backend_all on public.fx_rate
  for all to app_backend using (true) with check (true);
create policy backend_all on public.account_snapshot
  for all to app_backend using (true) with check (true);
create policy backend_all on public.position_snapshot
  for all to app_backend using (true) with check (true);
create policy backend_all on public.transaction
  for all to app_backend using (true) with check (true);
create policy backend_all on public.sync_state
  for all to app_backend using (true) with check (true);

-- ---------------------------------------------------------------------------
-- Realtime: the frontend's live sync indicator follows job and job_step changes.
-- (The publication exists on Supabase; a plain Postgres has none, so skip it there.)
-- ---------------------------------------------------------------------------
do $$
begin
  if exists (select from pg_catalog.pg_publication where pubname = 'supabase_realtime') then
    alter publication supabase_realtime add table public.job, public.job_step;
  end if;
end
$$;
