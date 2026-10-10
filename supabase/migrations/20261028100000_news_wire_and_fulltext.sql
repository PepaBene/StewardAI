-- News coverage plan, phases N0-N2 (docs/plans/news_coverage_analytics_improvement_plan.md).
--
-- - news_article: the wire lane. Every fresh item from every source is kept (headline,
--   summary, link, language), whether or not it matches a holding. `linked` says whether it also
--   became a news_item (a holding-linked story item); only linked items can alert.
-- - news_source_run: one row per source per ingest run (items in, stored, drops by reason,
--   latency, error), and the news_source_health view over it.
-- - source: region, language, paywall and state-media labels, and the fetch-health state a
--   conditional GET, backoff and circuit breaker need.
-- - news_fulltext: article text acquired through the fetch ladder, with its provenance
--   (rung, extractor, content hash) and a truncation flag so a paywall stub is never analysed
--   as the article. Text expires (retention), metadata stays.

-- ---------------------------------------------------------------------------
-- source: new types, labels and fetch health
-- ---------------------------------------------------------------------------
alter table public.source drop constraint if exists source_type_check;
alter table public.source add constraint source_type_check
  check (type in ('rss','page','edgar','finnhub','google_news','alpha_vantage','reference',
                  'macro','edgar_fts','rns','eqs','news_sitemap','gdelt','ai_search'));

alter table public.source
  add column region text check (region is null or region ~ '^[A-Z]{2}$'),
  add column language text,
  add column paywall text not null default 'open' check (paywall in ('open', 'metered', 'hard')),
  add column state_media boolean not null default false,
  add column etag text,
  add column http_last_modified text,
  add column consecutive_failures integer not null default 0 check (consecutive_failures >= 0),
  add column backoff_until timestamptz,
  add column last_success_at timestamptz,
  add column last_item_at timestamptz;

comment on column public.source.backoff_until is
  'Circuit breaker: the source is skipped until this time after repeated failures.';

-- ---------------------------------------------------------------------------
-- news_article: the wire
-- ---------------------------------------------------------------------------
create table public.news_article (
  id            bigint generated always as identity primary key,
  -- Canonical URL: one row per URL, ever (same canonicalisation as news_item.url).
  url           text        not null unique,
  source_id     text        not null references public.source (id),
  outlet        text,
  outlet_name   text,
  region        text check (region is null or region ~ '^[A-Z]{2}$'),
  tier          smallint    not null check (tier between 1 and 3),
  published_at  timestamptz not null,
  first_seen_at timestamptz not null default now(),
  headline      text        not null,
  summary       text,
  language      text,
  title_en      text,
  -- 64-bit SimHash of headline + summary, for near-duplicate detection (stored signed).
  simhash       bigint,
  -- True when the item also became a holding-linked news_item.
  linked        boolean     not null default false,
  news_item_id  bigint      references public.news_item (id),
  fetch_state   text        not null default 'headline'
                check (fetch_state in ('headline', 'summary', 'full', 'stub', 'failed', 'skipped')),
  job_id        bigint      references public.job (id),
  search        tsvector generated always as (
    setweight(to_tsvector('simple', coalesce(headline, '')), 'A')
      || setweight(to_tsvector('simple', coalesce(summary, '')), 'B')
  ) stored
);

comment on table public.news_article is
  'The wire lane: every fresh item from every enabled source, linked to a holding or not. Only items with linked = true feed stories and alerts.';

create index news_article_published_at on public.news_article (published_at desc);
create index news_article_source on public.news_article (source_id, published_at desc);
create index news_article_region on public.news_article (region, published_at desc);
create index news_article_search on public.news_article using gin (search);
create index news_article_simhash on public.news_article (simhash) where simhash is not null;
create index news_article_news_item on public.news_article (news_item_id)
  where news_item_id is not null;
create index news_article_job on public.news_article (job_id) where job_id is not null;

-- ---------------------------------------------------------------------------
-- news_source_run: per-source, per-run accounting (N0 instrumentation)
-- ---------------------------------------------------------------------------
create table public.news_source_run (
  id              bigint generated always as identity primary key,
  job_id          bigint      not null references public.job (id),
  source_id       text        not null references public.source (id),
  started_at      timestamptz not null default now(),
  latency_ms      integer     check (latency_ms is null or latency_ms >= 0),
  fetched         integer     not null default 0 check (fetched >= 0),
  stored_linked   integer     not null default 0 check (stored_linked >= 0),
  stored_wire     integer     not null default 0 check (stored_wire >= 0),
  -- Drop counts by reason: duplicate_url, stale, blocked, aggregator_not_allowed,
  -- tier3_excluded, not_modified, skipped_backoff.
  drops           jsonb       not null default '{}'::jsonb,
  error           text,
  unique (job_id, source_id)
);

comment on table public.news_source_run is
  'One row per source per news_ingest run: items in, stored by lane, drops by reason, latency and error.';

create index news_source_run_source on public.news_source_run (source_id, started_at desc);

create view public.news_source_health with (security_invoker = true) as
select
  s.id as source_id,
  s.name,
  s.type,
  s.tier,
  s.region,
  s.language,
  s.paywall,
  s.state_media,
  s.enabled,
  s.last_fetched_at,
  s.last_success_at,
  s.last_item_at,
  s.consecutive_failures,
  s.backoff_until,
  s.last_error,
  coalesce(r.runs, 0)            as runs_7d,
  coalesce(r.failed_runs, 0)     as failed_runs_7d,
  coalesce(r.fetched, 0)         as fetched_7d,
  coalesce(r.stored_linked, 0)   as stored_linked_7d,
  coalesce(r.stored_wire, 0)     as stored_wire_7d,
  r.avg_latency_ms,
  case
    when not s.enabled then 'disabled'
    when s.backoff_until is not null and s.backoff_until > now() then 'down'
    when s.consecutive_failures > 0 then 'degraded'
    when s.last_success_at is null then 'unknown'
    else 'ok'
  end as health
from public.source s
left join lateral (
  select count(*)                                    as runs,
         count(*) filter (where error is not null)   as failed_runs,
         sum(fetched)                                as fetched,
         sum(stored_linked)                          as stored_linked,
         sum(stored_wire)                            as stored_wire,
         round(avg(latency_ms))                      as avg_latency_ms
  from public.news_source_run
  where source_id = s.id and started_at >= now() - interval '7 days'
) r on true;

-- ---------------------------------------------------------------------------
-- news_fulltext: article text from the fetch ladder
-- ---------------------------------------------------------------------------
create table public.news_fulltext (
  id              bigint generated always as identity primary key,
  url             text        not null unique,
  article_id      bigint      references public.news_article (id),
  news_item_id    bigint      references public.news_item (id),
  -- 1 feed text, 2 polite fetch + extractor, 3 browser render, 4 official API.
  rung            smallint    not null check (rung between 1 and 4),
  extractor       text        not null,
  body            text,
  byline          text,
  language        text,
  authored_at     timestamptz,
  content_hash    text        not null,
  -- The text looks cut off (paywall stub, teaser, consent wall): not analysed as the article.
  truncated       boolean     not null default false,
  truncation_reason text,
  char_count      integer     not null check (char_count >= 0),
  fetched_at      timestamptz not null default now(),
  -- Retention: the body is cleared after this date unless the story is kept (linked to a
  -- holding, thesis or alert). The row and hash stay as the audit trail.
  expires_at      timestamptz,
  check ((body is null) or char_count = char_length(body))
);

comment on table public.news_fulltext is
  'Article text acquired through the fetch ladder, with provenance and a truncation flag. Bodies expire; the hash stays.';

create index news_fulltext_article on public.news_fulltext (article_id) where article_id is not null;
create index news_fulltext_item on public.news_fulltext (news_item_id) where news_item_id is not null;
create index news_fulltext_expiry on public.news_fulltext (expires_at) where body is not null;

-- ---------------------------------------------------------------------------
-- Privileges and row-level security (same pattern as news_item)
-- ---------------------------------------------------------------------------
alter table public.news_article enable row level security;
alter table public.news_source_run enable row level security;
alter table public.news_fulltext enable row level security;

revoke all on public.news_article, public.news_source_run, public.news_fulltext,
  public.news_source_health from anon, authenticated;
grant select on public.news_article, public.news_source_run, public.news_fulltext,
  public.news_source_health to authenticated;

create policy owner_read on public.news_article
  for select to authenticated using ((select private.is_owner()));
create policy owner_read on public.news_source_run
  for select to authenticated using ((select private.is_owner()));
create policy owner_read on public.news_fulltext
  for select to authenticated using ((select private.is_owner()));

-- Backend: the wire and the run log are append-and-update; full text is also cleared on expiry.
grant select, insert, update on public.news_article, public.news_source_run,
  public.news_fulltext to app_backend;
grant select on public.news_source_health to app_backend;

create policy backend_all on public.news_article
  for all to app_backend using (true) with check (true);
create policy backend_all on public.news_source_run
  for all to app_backend using (true) with check (true);
create policy backend_all on public.news_fulltext
  for all to app_backend using (true) with check (true);
