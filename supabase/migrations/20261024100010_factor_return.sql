-- Daily factor returns from the Ken French Data Library (quant plan Q4.1): the five Fama-French
-- factors and momentum for the Developed, Europe and US markets. Values are fractions per day
-- (the library publishes percent), in US dollars. Coverage per region and factor is read from
-- this table: the first and last date and the number of days.

create table public.factor_return (
  region text not null check (region in ('developed', 'europe', 'us')),
  factor text not null check (factor in ('MKT_RF', 'SMB', 'HML', 'RMW', 'CMA', 'MOM', 'RF')),
  date date not null,
  value double precision not null,
  fetched_at timestamptz not null default now(),
  primary key (region, factor, date)
);

alter table public.factor_return enable row level security;
revoke all on public.factor_return from anon, authenticated;
grant select on public.factor_return to authenticated;
grant select, insert, update, delete on public.factor_return to app_backend;
create policy owner_read on public.factor_return for select to authenticated
  using ((select private.is_owner()));
create policy backend_all on public.factor_return for all to app_backend
  using (true) with check (true);

comment on table public.factor_return is
  'Ken French Data Library daily factor returns, fraction per day, US dollars. Source: https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/data_library.html';
