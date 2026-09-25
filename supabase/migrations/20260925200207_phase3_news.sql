-- Phase 3: news pipeline, alerts and digests.
--
--   source         the feeds the news ingest reads: mirrors the `news_sources` config (name,
--                  tier, type, URL, enabled, scope), plus a trust score the feedback loop owns.
--   story_cluster  one story: an item and its syndicated copies (the same wire story in other
--                  outlets). Carries the event type and severity the classification gave it and
--                  the corroboration status (confirmed, unconfirmed, disputed, sentiment).
--   news_item      one article or filing, unique by canonical URL, linked to the holdings it
--                  matched and to its story cluster.
--   alert          one row per notification and channel: sent, held for quiet hours, suppressed
--                  (and why) or failed.
--   report         generated documents: daily and weekly digests now, research and reviews later.
--
-- Same access model as every other table: RLS on, owner-only SELECT for the frontend,
-- `anon` gets nothing, `app_backend` gets explicit grants plus a permissive policy.

-- ---------------------------------------------------------------------------
-- source
-- ---------------------------------------------------------------------------
create table public.source (
  id              text        primary key check (id ~ '^[a-z0-9]+(-[a-z0-9]+)*$'),
  name            text        not null,
  tier            smallint    not null check (tier between 1 and 3),
  type            text        not null check (type in ('rss', 'page', 'edgar', 'finnhub', 'google_news')),
  url             text,
  enabled         boolean     not null default true,
  -- Tickers the source is about; empty = any holding (items are matched by name).
  tickers         text[]      not null default '{}',
  -- 0..1. Lowered when the owner marks an item from this source wrong (Phase 4).
  trust_score     numeric     not null default 1 check (trust_score between 0 and 1),
  last_fetched_at timestamptz,
  last_item_count integer,
  last_error      text,
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now()
);

comment on table public.source is
  'News sources from the news_sources config, upserted by every ingest run; trust_score is kept across runs.';

create trigger source_set_updated_at
  before update on public.source
  for each row execute function private.set_updated_at();

-- ---------------------------------------------------------------------------
-- story_cluster
-- ---------------------------------------------------------------------------
create table public.story_cluster (
  id                 bigint generated always as identity primary key,
  -- The holding the story affects (from classification); null until classified.
  ticker             text,
  event_type         text,
  severity           smallint    check (severity between 0 and 3),
  -- pending: not yet classified. sentiment: tier-3 sources only.
  status             text        not null default 'pending'
                                 check (status in ('pending', 'confirmed', 'unconfirmed', 'disputed', 'sentiment')),
  -- The label the UI shows: verified (a tier-1 source states it), corroborated (confirmed by
  -- independent tier-2 outlets or a corroboration search), unconfirmed, disputed.
  confidence         text        not null default 'unconfirmed'
                                 check (confidence in ('verified', 'corroborated', 'unconfirmed', 'disputed')),
  status_reason      text,
  rumour             boolean     not null default false,
  -- The event is older than the article (a recycled story): never alerted.
  recycled           boolean     not null default false,
  event_date         date,
  kpi_id             text,
  headline           text        not null,
  headline_key       text        not null,
  best_tier          smallint    not null check (best_tier between 1 and 3),
  -- Outlets carrying this story. They are copies of one story, so corroboration counts
  -- them as a single source.
  source_count       integer     not null default 1,
  first_seen         timestamptz not null default now(),
  last_seen          timestamptz not null default now(),
  -- Corroboration search: rounds run, when, and what it found ({"supporting": [...], ...}).
  search_rounds      integer     not null default 0,
  last_searched_at   timestamptz,
  corroboration      jsonb,
  -- Unconfirmed stories are re-checked until then.
  recheck_until      timestamptz,
  config_version_ids bigint[]    not null default '{}',
  created_at         timestamptz not null default now(),
  updated_at         timestamptz not null default now()
);

comment on table public.story_cluster is
  'One story and its syndicated copies. Status follows the corroboration rules; severity follows the event_rules config.';

create index story_cluster_last_seen on public.story_cluster (last_seen desc);
create index story_cluster_ticker on public.story_cluster (ticker, last_seen desc);
create index story_cluster_recheck on public.story_cluster (recheck_until) where status = 'unconfirmed';

create trigger story_cluster_set_updated_at
  before update on public.story_cluster
  for each row execute function private.set_updated_at();

-- ---------------------------------------------------------------------------
-- news_item
-- ---------------------------------------------------------------------------
create table public.news_item (
  id               bigint generated always as identity primary key,
  -- Canonical URL (tracking parameters and fragments removed): one row per URL, ever.
  url              text        not null unique,
  source_id        text        not null references public.source (id),
  -- The publisher's domain and name (for aggregators, the outlet behind the item).
  outlet           text,
  outlet_name      text,
  tier             smallint    not null check (tier between 1 and 3),
  published_at     timestamptz not null,
  first_seen_at    timestamptz not null default now(),
  headline         text        not null,
  summary          text,
  language         text,
  cluster_id       bigint      references public.story_cluster (id),
  -- Holdings the item matched, and how: [{"ticker", "via": "name|alias|ticker|isin|source|
  -- competitor|supplier|customer|topic", "term"}].
  tickers          text[]      not null default '{}',
  links            jsonb       not null default '[]'::jsonb,
  -- Classification (null until classified).
  event_type       text,
  affected_ticker  text,
  thesis_link      text,
  rumour           boolean,
  relevant         boolean,
  event_date       date,
  classified_at    timestamptz,
  classified_by    text,
  job_id           bigint      references public.job (id)
);

comment on table public.news_item is
  'One article or filing per canonical URL. Items that match no holding are never stored.';

create index news_item_published_at on public.news_item (published_at desc);
create index news_item_cluster on public.news_item (cluster_id);
create index news_item_tickers on public.news_item using gin (tickers);
create index news_item_unclassified on public.news_item (first_seen_at) where classified_at is null;

-- ---------------------------------------------------------------------------
-- alert
-- ---------------------------------------------------------------------------
create table public.alert (
  id                bigint generated always as identity primary key,
  kind              text        not null check (kind in ('news', 'thesis_breach', 'test')),
  severity          smallint    not null check (severity between 0 and 3),
  ticker            text,
  event_type        text,
  cluster_id        bigint      references public.story_cluster (id),
  verdict_id        bigint      references public.verdict (id),
  channel           text        not null check (channel in ('telegram', 'email')),
  message           text        not null,
  -- pending: waiting for send_after (quiet hours) or the dispatcher; sent; suppressed (see
  -- suppressed_reason); failed (see error, retried up to a few attempts).
  status            text        not null default 'pending'
                                check (status in ('pending', 'sent', 'suppressed', 'failed')),
  send_after        timestamptz not null default now(),
  sent_at           timestamptz,
  suppressed_reason text,
  error             text,
  attempts          integer     not null default 0,
  config_version_id bigint      references public.config_version (id),
  job_id            bigint      references public.job (id),
  created_at        timestamptz not null default now()
);

comment on table public.alert is
  'Every notification decision, one row per channel, including the ones held or suppressed.';

-- A story or a verdict alerts at most once per channel.
create unique index alert_once_per_cluster on public.alert (cluster_id, channel)
  where cluster_id is not null and kind = 'news';
create unique index alert_once_per_verdict on public.alert (verdict_id, channel)
  where verdict_id is not null;
create index alert_due on public.alert (send_after) where status = 'pending';
create index alert_created_at on public.alert (created_at desc);

-- ---------------------------------------------------------------------------
-- report
-- ---------------------------------------------------------------------------
create table public.report (
  id                 bigint generated always as identity primary key,
  type               text        not null check (type in ('digest', 'research', 'review', 'escalation')),
  -- daily / weekly for digests.
  period             text,
  ticker             text,
  title              text        not null,
  -- Structured body: sections of sentences, each citing the news items and facts it rests on.
  body               jsonb       not null check (jsonb_typeof(body) = 'object'),
  body_md            text        not null,
  period_start       timestamptz,
  period_end         timestamptz,
  profile            text,
  config_version_ids bigint[]    not null default '{}',
  job_id             bigint      references public.job (id),
  -- Per-channel delivery outcome: {"email": {"status": "sent", "at": ...}, ...}.
  delivery           jsonb       not null default '{}'::jsonb,
  created_at         timestamptz not null default now()
);

create index report_type_created_at on public.report (type, period, created_at desc);

-- ---------------------------------------------------------------------------
-- Privileges and row-level security
-- ---------------------------------------------------------------------------
alter table public.source enable row level security;
alter table public.story_cluster enable row level security;
alter table public.news_item enable row level security;
alter table public.alert enable row level security;
alter table public.report enable row level security;

revoke all on public.source, public.story_cluster, public.news_item, public.alert, public.report
  from anon, authenticated;
grant select on public.source, public.story_cluster, public.news_item, public.alert, public.report
  to authenticated;

create policy owner_read on public.source
  for select to authenticated using ((select private.is_owner()));
create policy owner_read on public.story_cluster
  for select to authenticated using ((select private.is_owner()));
create policy owner_read on public.news_item
  for select to authenticated using ((select private.is_owner()));
create policy owner_read on public.alert
  for select to authenticated using ((select private.is_owner()));
create policy owner_read on public.report
  for select to authenticated using ((select private.is_owner()));

-- Backend: nothing is deleted; stories, items, alerts and reports change state only.
grant select, insert, update on public.source, public.story_cluster, public.news_item,
  public.alert, public.report to app_backend;

create policy backend_all on public.source
  for all to app_backend using (true) with check (true);
create policy backend_all on public.story_cluster
  for all to app_backend using (true) with check (true);
create policy backend_all on public.news_item
  for all to app_backend using (true) with check (true);
create policy backend_all on public.alert
  for all to app_backend using (true) with check (true);
create policy backend_all on public.report
  for all to app_backend using (true) with check (true);

-- Realtime: new alerts and digests reach the open app without a reload.
do $$
begin
  if exists (select from pg_catalog.pg_publication where pubname = 'supabase_realtime') then
    alter publication supabase_realtime add table public.alert, public.report;
  end if;
end
$$;
