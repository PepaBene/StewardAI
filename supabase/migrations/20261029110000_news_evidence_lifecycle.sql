-- News coverage plan, phase N5: evidence model and event lifecycle.
--
-- - news_item.evidence_kind / independence_key / is_correction: typed provenance of each item.
-- - story_cluster.lifecycle: reported, corroborated, primary_confirmed, disputed, corrected,
--   resolved. It is derived from the corroboration status and the evidence (steward.news.
--   evidence.lifecycle_for); lifecycle_closed_at is the owner's "resolved".
-- - story_lifecycle_event: every lifecycle change with its reason and the evidence behind it.
--   Append-only: corrections and disputes are kept, never overwritten.
-- - news_article.topics: portfolio watch topics a wire item mentions.

alter table public.news_item
  add column evidence_kind text
    check (evidence_kind is null or evidence_kind in ('primary_filing', 'regulator',
      'company_release', 'central_bank', 'wire', 'outlet', 'aggregator', 'lead')),
  add column independence_key text,
  add column is_correction boolean not null default false;

comment on column public.news_item.evidence_kind is
  'What kind of evidence the item is (filing, regulator, company release, central bank, wire, outlet, aggregator, AI lead). Null on items stored before N5.';
comment on column public.news_item.independence_key is
  'The outlet''s independence group (its syndication group''s first domain, else its host): items sharing a key count as one source.';

alter table public.story_cluster
  add column lifecycle text not null default 'reported'
    check (lifecycle in ('reported', 'corroborated', 'primary_confirmed', 'disputed',
                         'corrected', 'resolved')),
  add column lifecycle_reason text,
  add column lifecycle_at timestamptz,
  add column lifecycle_closed_at timestamptz;

alter table public.news_article
  add column topics text[] not null default '{}';

create index news_article_topics on public.news_article using gin (topics)
  where cardinality(topics) > 0;

create table public.story_lifecycle_event (
  id         bigint generated always as identity primary key,
  cluster_id bigint      not null references public.story_cluster (id) on delete cascade,
  at         timestamptz not null default now(),
  from_state text,
  to_state   text        not null
    check (to_state in ('reported', 'corroborated', 'primary_confirmed', 'disputed',
                        'corrected', 'resolved')),
  reason     text        not null,
  -- The items and search results behind the change: [{"url", "kind", "tier", "outlet"}].
  evidence   jsonb       not null default '[]'::jsonb,
  job_id     bigint      references public.job (id) on delete set null
);

comment on table public.story_lifecycle_event is
  'Append-only history of a story''s lifecycle: why it became corroborated, primary-confirmed, disputed, corrected or resolved, and from which evidence.';

create index story_lifecycle_event_cluster on public.story_lifecycle_event (cluster_id, at);
create index story_lifecycle_event_job on public.story_lifecycle_event (job_id)
  where job_id is not null;

alter table public.story_lifecycle_event enable row level security;
revoke all on public.story_lifecycle_event from anon, authenticated;
grant select on public.story_lifecycle_event to authenticated;
create policy owner_read on public.story_lifecycle_event
  for select to authenticated using ((select private.is_owner()));
grant select, insert on public.story_lifecycle_event to app_backend;
create policy backend_all on public.story_lifecycle_event
  for all to app_backend using (true) with check (true);
