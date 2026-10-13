-- Self-change control (autonomous engine plan, L5): everything the engine changes about itself
-- (a prompt, a threshold, a rule, a source weight, a schedule, a model) is first a proposal that
-- runs in shadow beside the current setting. The gates it must pass are written down with the
-- proposal, before any result exists, so they cannot be bent to fit the result.
create table public.engine_change (
  id          bigint generated always as identity primary key,
  target      text        not null check (length(target) between 1 and 120),
  change_kind text        not null check (change_kind in
                ('prompt', 'threshold', 'rule', 'source_weight', 'schedule', 'model')),
  hypothesis  text        not null,
  metric      text        not null,
  gates       jsonb       not null,
  before      jsonb       not null,
  after       jsonb       not null,
  baseline    jsonb,
  candidate   jsonb,
  status      text        not null default 'shadow' check (status in
                ('shadow', 'promoted', 'rejected', 'rolled_back')),
  verdict     text,
  created_at  timestamptz not null default now(),
  decided_at  timestamptz
);

create index engine_change_status on public.engine_change (status, created_at desc);
create index engine_change_target on public.engine_change (target, created_at desc);

comment on table public.engine_change is
  'A change the engine proposes to itself: shadow run, fixed gates, promotion and rollback.';

alter table public.engine_change enable row level security;
revoke all on public.engine_change from anon, authenticated;
grant select on public.engine_change to authenticated;
create policy owner_read on public.engine_change
  for select to authenticated using ((select private.is_owner()));
grant select, insert, update on public.engine_change to app_backend;
create policy backend_all on public.engine_change
  for all to app_backend using (true) with check (true);
grant usage, select on sequence public.engine_change_id_seq to app_backend;
