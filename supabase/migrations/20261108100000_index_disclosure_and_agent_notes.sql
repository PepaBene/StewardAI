-- More of the app's prose enters the passage index, more events can start an inbox run, and
-- the owner's agent notes keep a history.
--
-- document_chunk.kind: add 'disclosure' (a relationship passage or a disclosure alert) and
-- 'agent_note' (the owner's working notes). inbox_item.trigger_kind: add 'disclosure' (an open
-- disclosure alert), 'breach' (an open portfolio breach: a band, hard limit, risk limit or
-- strategy rule) and 'verdict' (a thesis KPI verdict that came out as a breach).
-- Only the check constraints change for these; no row is touched.
alter table public.document_chunk drop constraint document_chunk_kind_check;
alter table public.document_chunk add constraint document_chunk_kind_check
  check (kind in ('filing', 'report', 'thesis', 'note', 'news', 'disclosure', 'agent_note'));

alter table public.inbox_item drop constraint inbox_item_trigger_kind_check;
alter table public.inbox_item add constraint inbox_item_trigger_kind_check
  check (trigger_kind in ('escalation', 'alert', 'disclosure', 'breach', 'verdict'));

-- agent_note_version: every text, ticker or active change of a note, and its deletion, keeps
-- the earlier state here. note_id has no foreign key on purpose: the history outlives a deleted
-- note, so an owner can see what a note said before it was removed.
create table public.agent_note_version (
  id          bigint generated always as identity primary key,
  note_id     bigint      not null,
  ticker      text,
  text        text        not null,
  active      boolean     not null,
  change      text        not null check (change in ('created', 'edited', 'deleted')),
  changed_at  timestamptz not null default now()
);

create index agent_note_version_note on public.agent_note_version (note_id, id desc);

comment on table public.agent_note_version is
  'The state of an agent note after each change, newest last. Kept after the note is deleted.';

alter table public.agent_note_version enable row level security;
revoke all on public.agent_note_version from anon, authenticated;
grant select on public.agent_note_version to authenticated;
create policy owner_read on public.agent_note_version
  for select to authenticated using ((select private.is_owner()));
grant select, insert on public.agent_note_version to app_backend;
create policy backend_all on public.agent_note_version
  for all to app_backend using (true) with check (true);
