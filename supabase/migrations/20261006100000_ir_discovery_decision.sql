-- Audit log of investor-relations page discovery: one row per holding per attempt, with what
-- was proposed, what the checks and the AI approver said, and what was applied. The latest
-- row per ticker also sets the cooldown before the holding is searched again.
create table public.ir_discovery_decision (
  id           bigint generated always as identity primary key,
  ticker       text not null,
  attempted_at timestamptz not null default now(),
  outcome      text not null check (outcome in ('applied', 'left', 'none', 'error')),
  config_id    integer,
  detail       jsonb not null default '{}'::jsonb
);
create index ir_discovery_decision_ticker on public.ir_discovery_decision (ticker, attempted_at desc);
alter table public.ir_discovery_decision enable row level security;
grant select, insert, update, delete on public.ir_discovery_decision to app_backend;
grant select on public.ir_discovery_decision to authenticated;
create policy backend_all on public.ir_discovery_decision for all to app_backend
  using (true) with check (true);
create policy owner_read on public.ir_discovery_decision for select to authenticated
  using ((select private.is_owner()));
