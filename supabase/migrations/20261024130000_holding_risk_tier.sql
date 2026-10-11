-- The risk tier of each holding, worked out from its own numbers (volatility, market cap,
-- profitability) by the rules in the strategy's risk section. The strategy names no holdings;
-- an owner entry in the strategy's classifications still wins over what is stored here.

create table public.holding_risk_tier (
  ticker text primary key,
  tier text not null check (tier in ('core', 'growth', 'speculative')),
  kind text not null check (kind in ('stock', 'fund')),
  volatility double precision,
  market_cap double precision,
  profitable boolean,
  reason text not null,
  computed_at timestamptz not null default now()
);

alter table public.holding_risk_tier enable row level security;
revoke all on public.holding_risk_tier from anon, authenticated;
grant select on public.holding_risk_tier to authenticated;
grant select, insert, update, delete on public.holding_risk_tier to app_backend;
create policy owner_read on public.holding_risk_tier for select to authenticated
  using ((select private.is_owner()));
create policy backend_all on public.holding_risk_tier for all to app_backend
  using (true) with check (true);
