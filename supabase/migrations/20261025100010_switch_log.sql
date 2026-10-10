-- Q8.9: the switch log. One row is a switch the owner confirmed: a stock sold and another bought
-- with the proceeds, with the prices at the time and the figures that justified it (expected
-- returns, risk profiles, hurdle, lots and tax). The yearly scorecard follows the bought stock
-- against the sold one from the switch date. Append-only like the decision journal.

create table public.switch_log (
  id bigint generated always as identity primary key,
  owner_id uuid not null references auth.users(id) on delete cascade,
  switched_on date not null default current_date,
  sold_ticker text not null check (length(trim(sold_ticker)) between 1 and 40),
  bought_ticker text not null check (length(trim(bought_ticker)) between 1 and 40),
  sold_price numeric check (sold_price is null or sold_price > 0),
  sold_currency text,
  bought_price numeric check (bought_price is null or bought_price > 0),
  bought_currency text,
  sold_value_base numeric check (sold_value_base is null or sold_value_base > 0),
  bought_value_base numeric check (bought_value_base is null or bought_value_base > 0),
  snapshot jsonb not null default '{}' check (jsonb_typeof(snapshot) = 'object'),
  note text check (note is null or length(note) <= 2000),
  created_at timestamptz not null default now(),
  check (sold_ticker <> bought_ticker)
);
create index switch_log_owner on public.switch_log (owner_id, switched_on desc, id desc);

alter table public.switch_log enable row level security;
revoke all on public.switch_log from anon;
grant select, insert on public.switch_log to authenticated, app_backend;
create policy switch_log_owner_read on public.switch_log for select to authenticated
  using (owner_id = (select auth.uid()));
create policy switch_log_owner_insert on public.switch_log for insert to authenticated
  with check (owner_id = (select auth.uid()));
create policy switch_log_backend on public.switch_log for all to app_backend
  using (true) with check (true);
grant usage, select on sequence public.switch_log_id_seq to authenticated, app_backend;

comment on table public.switch_log is
  'Switches the owner confirmed (stock sold, stock bought) with the figures behind them. Append-only; the scorecard compares the two from the switch date.';
