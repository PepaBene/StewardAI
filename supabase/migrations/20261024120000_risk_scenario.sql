-- Saved what-if scenarios for the factor-shock tool (quant plan Q3.3): a name and the factor
-- moves and owner assumptions (betas, fallback beta) the owner stated. Each run of a saved
-- scenario is kept separately in public.risk_report_snapshot, kind 'shock'.

create table public.risk_scenario (
  id bigint generated always as identity primary key,
  name text not null unique check (length(name) between 1 and 80),
  spec jsonb not null check (jsonb_typeof(spec) = 'object'),
  created_at timestamptz not null default now()
);

alter table public.risk_scenario enable row level security;
revoke all on public.risk_scenario from anon, authenticated;
grant select on public.risk_scenario to authenticated;
grant select, insert, update, delete on public.risk_scenario to app_backend;
create policy owner_read on public.risk_scenario for select to authenticated
  using ((select private.is_owner()));
create policy backend_all on public.risk_scenario for all to app_backend
  using (true) with check (true);
