-- The gates a self-change must pass (autonomous engine plan, L5) in a row the owner edits. Only
-- the owner-authenticated API route writes it; the engine reads it and cannot propose a change
-- to it (`gates` is on the freeze list). One row, replaced on each save, with who saved it.
create table public.engine_gate_setting (
  name        text        primary key check (name = 'floor'),
  body        jsonb       not null,
  updated_at  timestamptz not null default now(),
  updated_by  uuid
);

alter table public.engine_gate_setting enable row level security;
revoke all on public.engine_gate_setting from anon, authenticated;
grant select on public.engine_gate_setting to authenticated;
create policy owner_read on public.engine_gate_setting
  for select to authenticated using ((select private.is_owner()));
grant select, insert, update on public.engine_gate_setting to app_backend;
create policy backend_all on public.engine_gate_setting
  for all to app_backend using (true) with check (true);
