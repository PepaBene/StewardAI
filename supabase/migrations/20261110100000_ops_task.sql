-- Ops watchdog (autonomy plan, phase D): one row per problem the app is fixing on its own or
-- needs the owner for.
--
-- status 'fixing': the watchdog has acted (re-ran a refresh, retried a job, lifted a block) and
-- is waiting to see whether the problem is gone. 'needs_owner': it ran out of attempts or the
-- problem is one only the owner can fix (a missing API key); this is what "Needs you" lists.
-- 'resolved': the problem is gone. At most one open row per (kind, subject).
create table public.ops_task (
  id             bigint generated always as identity primary key,
  kind           text        not null,
  subject        text        not null,
  status         text        not null check (status in ('fixing', 'needs_owner', 'resolved')),
  title          text        not null,
  detail         text        not null default '',
  -- Where the owner goes to deal with it (an app path or a signup page).
  href           text,
  -- Share of the portfolio it touches, or a priority between 0 and 1, to rank the list.
  weight         real,
  attempts       integer     not null default 0,
  last_action    text,
  last_action_at timestamptz,
  first_seen     timestamptz not null default now(),
  last_seen      timestamptz not null default now(),
  resolved_at    timestamptz
);

create unique index ops_task_one_open on public.ops_task (kind, subject)
  where status <> 'resolved';
create index ops_task_status on public.ops_task (status, last_seen desc);

comment on table public.ops_task is
  'Problems the ops watchdog is fixing or has handed to the owner, with its attempts.';

alter table public.ops_task enable row level security;
revoke all on public.ops_task from anon, authenticated;
grant select on public.ops_task to authenticated;
create policy owner_read on public.ops_task
  for select to authenticated using ((select private.is_owner()));
grant select, insert, update on public.ops_task to app_backend;
create policy backend_all on public.ops_task
  for all to app_backend using (true) with check (true);
