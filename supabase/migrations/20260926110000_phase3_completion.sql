-- Phase 3 completion: the tasks added by the plan's gap review and the additional sources.
--
--   macro_series      macro figures from FRED, the ECB Data Portal and Eurostat, each with its
--                     release date and source URL (digests cite both; the AI never supplies a
--                     macro number).
--   filing_hit        SEC EDGAR full-text search hits: watch-topic mentions (news items) and
--                     other filers naming a holding (suggested customer/supplier links).
--   portfolio_breach  drift, hard-limit and strategy-rule breaches after each sync: open while
--                     out of band, cleared when back; alerted once per breach.
--   story_cluster     + duplicate_of (a known event seen again never alerts twice) and the
--                     owner's resolution of a disputed story.
--   alert             + kinds 'portfolio' and 'system' (failed jobs, missed runs, coverage
--                     gaps) and a dedupe_key so each such condition alerts once.
--   source            + types rns, eqs (regulatory news), edgar_fts (full-text search) and
--                     macro (macro surprises raised from macro_series).
--
-- Same access model as every other table: RLS on, owner-only SELECT for the frontend,
-- `anon` gets nothing, `app_backend` gets explicit grants plus a permissive policy.

create table public.macro_series (
  series_id    text        not null,
  source       text        not null check (source in ('fred', 'ecb', 'eurostat')),
  period       date        not null,
  value        numeric     not null,
  label        text,
  unit         text,
  -- When the figure was published (Eurostat: the dataset's update date; FRED: the
  -- observation's realtime start; ECB: first seen), and where.
  release_date date,
  url          text        not null,
  fetched_at   timestamptz not null default now(),
  primary key (series_id, source, period)
);

create index macro_series_release on public.macro_series (release_date desc);

create table public.filing_hit (
  id            bigint generated always as identity primary key,
  ticker        text        not null,
  -- topic: a watch topic; customer / supplier: another filer naming the holding.
  kind          text        not null check (kind in ('topic', 'customer', 'supplier')),
  query         text        not null,
  matched_term  text        not null,
  accession     text        not null,
  cik           text,
  filer_name    text,
  form          text,
  filed_at      date,
  file          text,
  url           text        not null,
  news_item_id  bigint      references public.news_item (id),
  relationship_id bigint    references public.entity_relationship (id),
  created_at    timestamptz not null default now(),
  unique (ticker, kind, matched_term, accession, file)
);

create table public.portfolio_breach (
  id            bigint generated always as identity primary key,
  -- position:<ticker>, category:<dimension>:<bucket>, cash, rule:<rule id>[:<scope>]
  key           text        not null,
  kind          text        not null check (kind in ('band', 'hard_limit', 'rule')),
  ticker        text,
  severity      smallint    not null check (severity between 0 and 3),
  detail        text        not null,
  observed      numeric,
  limit_value   numeric,
  first_seen    timestamptz not null default now(),
  last_seen     timestamptz not null default now(),
  cleared_at    timestamptz,
  first_job_id  bigint      references public.job (id),
  last_job_id   bigint      references public.job (id),
  config_version_id bigint  references public.config_version (id)
);

comment on table public.portfolio_breach is
  'Open while cleared_at is null. A breach alerts when it opens, never again until it clears.';

create unique index portfolio_breach_open on public.portfolio_breach (key) where cleared_at is null;

alter table public.story_cluster
  add column duplicate_of bigint references public.story_cluster (id),
  add column resolved_at  timestamptz,
  add column resolution   text check (resolution in ('confirmed', 'false')),
  add column resolution_note text;

comment on column public.story_cluster.duplicate_of is
  'An earlier story about the same holding, event type and event: this one never alerts.';

alter table public.alert drop constraint alert_kind_check;
alter table public.alert add constraint alert_kind_check
  check (kind in ('news', 'thesis_breach', 'test', 'portfolio', 'system'));
alter table public.alert add column dedupe_key text;
alter table public.alert add column breach_id bigint references public.portfolio_breach (id);
create unique index alert_once_per_key on public.alert (dedupe_key, channel)
  where dedupe_key is not null;

alter table public.source drop constraint source_type_check;
alter table public.source add constraint source_type_check
  check (type in ('rss', 'page', 'edgar', 'finnhub', 'google_news', 'reference', 'rns', 'eqs',
                  'edgar_fts', 'macro'));

-- ---------------------------------------------------------------------------
-- Privileges and row-level security
-- ---------------------------------------------------------------------------
alter table public.macro_series enable row level security;
alter table public.filing_hit enable row level security;
alter table public.portfolio_breach enable row level security;

revoke all on public.macro_series, public.filing_hit, public.portfolio_breach
  from anon, authenticated;
grant select on public.macro_series, public.filing_hit, public.portfolio_breach
  to authenticated;

create policy owner_read on public.macro_series
  for select to authenticated using ((select private.is_owner()));
create policy owner_read on public.filing_hit
  for select to authenticated using ((select private.is_owner()));
create policy owner_read on public.portfolio_breach
  for select to authenticated using ((select private.is_owner()));

grant select, insert, update on public.macro_series, public.filing_hit, public.portfolio_breach
  to app_backend;

create policy backend_all on public.macro_series
  for all to app_backend using (true) with check (true);
create policy backend_all on public.filing_hit
  for all to app_backend using (true) with check (true);
create policy backend_all on public.portfolio_breach
  for all to app_backend using (true) with check (true);
