-- The owner's handling of the ops report's incidents: a severe story nobody was alerted to, or an
-- alert marked not useful, is open until the owner acknowledges or resolves it.
create table public.news_incident_review (
  kind       text        not null check (kind in ('unalerted_severe', 'alert_not_useful')),
  story_id   bigint      not null references public.story_cluster (id) on delete cascade,
  status     text        not null default 'open'
    check (status in ('open', 'acknowledged', 'resolved')),
  note       text,
  updated_at timestamptz not null default now(),
  primary key (kind, story_id)
);

comment on table public.news_incident_review is
  'Owner status and note per ops incident. No row means open. Resolved incidents leave the report.';

alter table public.news_incident_review enable row level security;
revoke all on public.news_incident_review from anon, authenticated;
grant select on public.news_incident_review to authenticated;
create policy owner_read on public.news_incident_review
  for select to authenticated using ((select private.is_owner()));
grant select, insert, update on public.news_incident_review to app_backend;
create policy backend_all on public.news_incident_review
  for all to app_backend using (true) with check (true);
