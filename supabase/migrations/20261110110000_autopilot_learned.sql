-- Autopilot learning (autonomy plan, E1): the confidence a kind of decision has learned to ask
-- for after being judged wrong. The weekly calibration job sets it from the kind's recent wrong
-- decisions (the owner's reverts and spot checks, and the second model's audit) and clears it
-- once the kind has been right for a while. The judge uses the larger of this and the Jobs
-- config's `min_confidence`, so learning can only make the autopilot more careful.
create table public.autopilot_learned (
  kind           text        primary key,
  min_confidence real        not null check (min_confidence between 0.5 and 0.99),
  based_on       integer     not null check (based_on > 0),
  reason         text        not null,
  updated_at     timestamptz not null default now()
);

comment on table public.autopilot_learned is
  'The confidence floor the autopilot learned for a kind after wrong decisions; only ever raises the Jobs config floor.';

alter table public.autopilot_learned enable row level security;
revoke all on public.autopilot_learned from anon, authenticated;
grant select on public.autopilot_learned to authenticated;
create policy owner_read on public.autopilot_learned
  for select to authenticated using ((select private.is_owner()));
grant select, insert, update, delete on public.autopilot_learned to app_backend;
create policy backend_all on public.autopilot_learned
  for all to app_backend using (true) with check (true);
