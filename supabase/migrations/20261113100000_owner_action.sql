-- Owner actions (autonomous engine plan L7): every change the owner makes to what the engine did,
-- whether from a screen or from chat, goes through one registry and leaves one row here. A chat
-- change is first `proposed` (a confirmation card), then `done` when the owner confirms.
create table public.owner_action (
  id         bigint generated always as identity primary key,
  action     text        not null check (length(action) between 1 and 80),
  args       jsonb       not null default '{}'::jsonb,
  summary    text        not null,
  risk       text        not null check (risk in ('low', 'medium', 'high')),
  status     text        not null check (status in ('proposed', 'done', 'cancelled', 'failed')),
  via        text        not null check (via in ('screen', 'chat')),
  reason     text,
  result     jsonb,
  created_at timestamptz not null default now(),
  decided_at timestamptz
);

create index owner_action_status on public.owner_action (status, created_at desc);
create index owner_action_action on public.owner_action (action, created_at desc);

comment on table public.owner_action is
  'Owner changes to engine output, proposed and confirmed through one registry (screen or chat).';

alter table public.owner_action enable row level security;
revoke all on public.owner_action from anon, authenticated;
grant select on public.owner_action to authenticated;
create policy owner_read on public.owner_action
  for select to authenticated using ((select private.is_owner()));
grant select, insert, update on public.owner_action to app_backend;
create policy backend_all on public.owner_action
  for all to app_backend using (true) with check (true);
grant usage, select on sequence public.owner_action_id_seq to app_backend;
