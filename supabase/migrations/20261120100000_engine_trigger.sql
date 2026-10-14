-- Proactive triggers (autonomous engine plan, L4): every time the engine starts work on its own
-- (a research run for a stale holding, a drafted thesis for a gap, a heads-up before a report, a
-- check after one, a kind put back to shadow) it is recorded here with the reason it started, or
-- with the reason it was held back (cooldown, daily cap, cost cap, budget, or the owner dismissing
-- the same kind of notice). Append-only.
create table public.engine_trigger (
  id          bigint generated always as identity primary key,
  created_at  timestamptz not null default now(),
  kind        text        not null check (kind in
                ('staleness', 'event', 'coverage', 'anticipation', 'learning')),
  subject     text        not null check (length(subject) between 1 and 200),
  reason      text        not null check (length(reason) between 1 and 1000),
  action      text        not null check (length(action) between 1 and 200),
  status      text        not null check (status in ('fired', 'skipped')),
  skip_reason text,
  job_id      bigint
);

create index engine_trigger_recent on public.engine_trigger (created_at desc);
create index engine_trigger_subject on public.engine_trigger (kind, subject, created_at desc);

comment on table public.engine_trigger is
  'Append-only record of work the engine started on its own, with the reason, or why it held back.';

alter table public.engine_trigger enable row level security;
revoke all on public.engine_trigger from anon, authenticated;
grant select on public.engine_trigger to authenticated;
create policy owner_read on public.engine_trigger
  for select to authenticated using ((select private.is_owner()));
grant select, insert on public.engine_trigger to app_backend;
create policy backend_read on public.engine_trigger for select to app_backend using (true);
create policy backend_insert on public.engine_trigger
  for insert to app_backend with check (true);
grant usage, select on sequence public.engine_trigger_id_seq to app_backend;
