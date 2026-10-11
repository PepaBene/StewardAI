-- The broker snapshots start on the first day the app synced (2026-09-27), but the transactions
-- reach back to 2026-01-09, so the Risk and Performance pages had 5 to 14 days of history. The
-- days before the first real snapshot are rebuilt from the ledger (quantities and cash, which
-- reproduce the first real snapshot to the cent) and the stored daily closes.
--
-- Reconstructed rows live in their own tables so account_snapshot and position_snapshot keep
-- holding only what Trading212 reported. The two history views put them together; a
-- reconstructed day exists only before the first real snapshot, and every row says so.

create table public.account_snapshot_reconstructed (
  snapshot_date    date primary key,
  account_currency text    not null,
  base_currency    text    not null,
  fx_source        text    not null,
  fx_rate          numeric not null default 1,
  cash_base        numeric not null,
  invested_base    numeric not null,
  total_base       numeric not null,
  invested_cost    numeric not null,
  unrealized_ppl   numeric not null,
  built_at         timestamptz not null default now()
);

create table public.position_snapshot_reconstructed (
  snapshot_date       date    not null,
  ticker              text    not null,
  quantity            numeric not null,
  avg_price           numeric not null,
  current_price       numeric not null,
  instrument_currency text    not null,
  fx_rate             numeric not null,
  value_base          numeric not null,
  base_currency       text    not null,
  price_basis         text    not null check (price_basis in ('close', 'cost')),
  primary key (snapshot_date, ticker)
);

comment on table public.account_snapshot_reconstructed is
  'Daily account totals rebuilt from the transaction ledger and stored closes for the days before the first Trading212 snapshot. Not broker data.';
comment on column public.position_snapshot_reconstructed.price_basis is
  'close: the stored close of that day (or the last one before it); cost: no stored series, the average cost is used.';

alter table public.account_snapshot_reconstructed enable row level security;
alter table public.position_snapshot_reconstructed enable row level security;

revoke all on public.account_snapshot_reconstructed, public.position_snapshot_reconstructed
  from anon, authenticated;
grant select on public.account_snapshot_reconstructed, public.position_snapshot_reconstructed
  to authenticated;
grant select, insert, update, delete
  on public.account_snapshot_reconstructed, public.position_snapshot_reconstructed
  to app_backend;

create policy owner_read on public.account_snapshot_reconstructed
  for select to authenticated using ((select private.is_owner()));
create policy owner_read on public.position_snapshot_reconstructed
  for select to authenticated using ((select private.is_owner()));
create policy backend_all on public.account_snapshot_reconstructed
  for all to app_backend using (true) with check (true);
create policy backend_all on public.position_snapshot_reconstructed
  for all to app_backend using (true) with check (true);

create view public.account_history with (security_invoker = true) as
select snapshot_date, captured_at, total_base, cash_base, invested_base, account_currency,
       base_currency, fx_rate, total_value, invested_value, unrealized_ppl,
       false as reconstructed
from public.account_snapshot
union all
select r.snapshot_date, (r.snapshot_date + time '20:37') at time zone 'UTC', r.total_base, r.cash_base, r.invested_base,
       r.account_currency, r.base_currency, r.fx_rate, r.total_base, r.invested_base,
       r.unrealized_ppl, true
from public.account_snapshot_reconstructed r
where r.snapshot_date < (select min(snapshot_date) from public.account_snapshot);

create view public.position_history with (security_invoker = true) as
select snapshot_date, ticker, quantity, avg_price, current_price, fx_rate, value_base,
       base_currency, false as reconstructed
from public.position_snapshot
union all
select r.snapshot_date, r.ticker, r.quantity, r.avg_price, r.current_price, r.fx_rate,
       r.value_base, r.base_currency, true
from public.position_snapshot_reconstructed r
where r.snapshot_date < (select min(snapshot_date) from public.position_snapshot);

grant select on public.account_history, public.position_history to authenticated, app_backend;
