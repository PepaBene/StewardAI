-- Engagement (autonomy plan E2): what the owner opens. The app learns from this which stories,
-- reports and notices are read and which are never touched, so it can send less of the second
-- kind. One row per open; no content is stored, only what was opened and when.
create table public.engagement_event (
  id         bigint generated always as identity primary key,
  kind       text        not null check (kind in
               ('news_story', 'report', 'notice', 'escalation', 'thesis', 'company', 'fund')),
  -- The thing opened: a cluster or report id, a ticker, or a notice id, as text.
  ref        text        not null check (length(ref) between 1 and 120),
  ticker     text,
  created_at timestamptz not null default now()
);

create index engagement_event_kind on public.engagement_event (kind, created_at desc);
create index engagement_event_ref on public.engagement_event (kind, ref);

comment on table public.engagement_event is
  'One row each time the owner opens a story, report, notice or page; used to tune volume.';

alter table public.engagement_event enable row level security;
revoke all on public.engagement_event from anon, authenticated;
grant select on public.engagement_event to authenticated;
create policy owner_read on public.engagement_event
  for select to authenticated using ((select private.is_owner()));
grant select, insert on public.engagement_event to app_backend;
create policy backend_all on public.engagement_event
  for all to app_backend using (true) with check (true);
grant usage, select on sequence public.engagement_event_id_seq to app_backend;
