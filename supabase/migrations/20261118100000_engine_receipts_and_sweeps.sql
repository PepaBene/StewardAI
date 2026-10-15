-- Delivery receipts and sweep history (autonomous engine plan, phase 1).
-- An alert keeps the provider's message id and, for email, the provider's last event, so "sent"
-- means more than "the API accepted it". A sweep run is recorded so the weekly re-check runs
-- weekly and its disagreement rate can be read over time.
alter table public.alert
  add column provider_id         text,
  add column provider_status     text,
  add column provider_checked_at timestamptz;

create table public.engine_sweep (
  id      bigint generated always as identity primary key,
  name    text        not null check (length(name) between 1 and 80),
  ran_at  timestamptz not null default now(),
  checked integer     not null check (checked >= 0),
  flagged integer     not null check (flagged >= 0),
  detail  jsonb
);

create index engine_sweep_name on public.engine_sweep (name, ran_at desc);

comment on table public.engine_sweep is
  'One row per run of an engine sweep (re-verification): how many it checked and flagged.';

alter table public.engine_sweep enable row level security;
revoke all on public.engine_sweep from anon, authenticated;
grant select on public.engine_sweep to authenticated;
create policy owner_read on public.engine_sweep
  for select to authenticated using ((select private.is_owner()));
grant select, insert on public.engine_sweep to app_backend;
create policy backend_read on public.engine_sweep for select to app_backend using (true);
create policy backend_insert on public.engine_sweep
  for insert to app_backend with check (true);
grant usage, select on sequence public.engine_sweep_id_seq to app_backend;
