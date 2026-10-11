-- News coverage plan, phases N6 and N7: the analysis engine and its surface.
--
-- - story_analysis: one append-only row per technique result for a story (extraction, novelty,
--   impact chains, thread diff, framing comparison, claim check, market context, thesis link).
--   The unique key makes a re-run on unchanged input a no-op; a changed input adds a row and the
--   latest row per (story, kind) is the current one.
-- - story_thread / story_thread_update: a developing situation and what each update changed.
-- - news_theme_day: daily story and wire volume per portfolio watch theme (regime flags).
-- - news_brief: morning, weekly and per-holding briefs, every line citing stored items.
-- - news_feedback: the owner's marks on items, stories, claims and brief lines. They feed the
--   benchmark; they are never used to train a model.

create table public.story_analysis (
  id         bigint generated always as identity primary key,
  cluster_id bigint      not null references public.story_cluster (id) on delete cascade,
  kind       text        not null
    check (kind in ('extraction', 'novelty', 'impact', 'thread', 'framing', 'claims',
                    'market', 'thesis')),
  -- Hash of everything the result was computed from (text, model, prompt): same input, same row.
  input_hash text        not null,
  -- Null for results computed in code.
  model      text,
  payload    jsonb       not null,
  created_at timestamptz not null default now(),
  job_id     bigint      references public.job (id) on delete set null,
  unique (cluster_id, kind, input_hash)
);

comment on table public.story_analysis is
  'Append-only technique results per story (N6). The newest row per (cluster_id, kind) is current. Facts carry spans verified against the stored text before they are saved.';

create index story_analysis_latest on public.story_analysis (cluster_id, kind, created_at desc);
create index story_analysis_job on public.story_analysis (job_id) where job_id is not null;

create table public.story_thread (
  id             bigint generated always as identity primary key,
  ticker         text,
  title          text        not null,
  opened_at      timestamptz not null default now(),
  last_update_at timestamptz not null default now(),
  state          text        not null default 'open' check (state in ('open', 'quiet', 'closed'))
);

comment on table public.story_thread is
  'A developing situation (a merger, a strike, a tariff dispute): the stories that belong to it.';

create index story_thread_ticker on public.story_thread (ticker, last_update_at desc);

alter table public.story_cluster
  add column thread_id bigint references public.story_thread (id) on delete set null;

create index story_cluster_thread on public.story_cluster (thread_id) where thread_id is not null;

create table public.story_thread_update (
  id         bigint generated always as identity primary key,
  thread_id  bigint      not null references public.story_thread (id) on delete cascade,
  cluster_id bigint      not null references public.story_cluster (id) on delete cascade,
  at         timestamptz not null default now(),
  -- {"new": [...], "changed": [...], "retracted": [...], "unknown": [...]} against the
  -- thread's previous state.
  diff       jsonb       not null,
  unique (thread_id, cluster_id)
);

create index story_thread_update_cluster on public.story_thread_update (cluster_id);

create table public.news_theme_day (
  theme      text        not null,
  day        date        not null,
  region     text        not null default '',
  wire_items integer     not null default 0 check (wire_items >= 0),
  stories    integer     not null default 0 check (stories >= 0),
  -- Set when the day's volume is far above the theme's recent level ("spike").
  flag       text        check (flag is null or flag in ('spike', 'spread')),
  primary key (theme, day, region)
);

comment on table public.news_theme_day is
  'Daily wire and story counts per portfolio watch theme and region, with regime flags.';

create table public.news_brief (
  id           bigint generated always as identity primary key,
  kind         text        not null check (kind in ('morning', 'weekly', 'holding')),
  ticker       text        not null default '',
  period_start timestamptz not null,
  period_end   timestamptz not null,
  -- {"sections": [{"title", "lines": [{"text", "item_ids": [...], "why", "watch"}]}]}
  body         jsonb       not null,
  model        text,
  created_at   timestamptz not null default now(),
  job_id       bigint      references public.job (id) on delete set null,
  unique (kind, ticker, period_start)
);

comment on table public.news_brief is
  'Written briefs. Every line cites stored news items; a line that cites nothing is not saved.';

create index news_brief_recent on public.news_brief (kind, period_end desc);
create index news_brief_job on public.news_brief (job_id) where job_id is not null;

create table public.news_feedback (
  id         bigint generated always as identity primary key,
  target     text        not null check (target in ('item', 'story', 'claim', 'brief_line')),
  target_id  text        not null,
  kind       text        not null
    check (kind in ('wrong_entity', 'duplicate', 'misread', 'missing_source', 'useful',
                    'not_useful')),
  note       text,
  created_at timestamptz not null default now()
);

comment on table public.news_feedback is
  'Owner feedback on news items, stories, claims and brief lines. Input to the benchmark only.';

create index news_feedback_target on public.news_feedback (target, target_id);

-- Privileges and row-level security (same pattern as story_lifecycle_event).
alter table public.story_analysis enable row level security;
alter table public.story_thread enable row level security;
alter table public.story_thread_update enable row level security;
alter table public.news_theme_day enable row level security;
alter table public.news_brief enable row level security;
alter table public.news_feedback enable row level security;

revoke all on public.story_analysis, public.story_thread, public.story_thread_update,
  public.news_theme_day, public.news_brief, public.news_feedback from anon, authenticated;
grant select on public.story_analysis, public.story_thread, public.story_thread_update,
  public.news_theme_day, public.news_brief, public.news_feedback to authenticated;

create policy owner_read on public.story_analysis
  for select to authenticated using ((select private.is_owner()));
create policy owner_read on public.story_thread
  for select to authenticated using ((select private.is_owner()));
create policy owner_read on public.story_thread_update
  for select to authenticated using ((select private.is_owner()));
create policy owner_read on public.news_theme_day
  for select to authenticated using ((select private.is_owner()));
create policy owner_read on public.news_brief
  for select to authenticated using ((select private.is_owner()));
create policy owner_read on public.news_feedback
  for select to authenticated using ((select private.is_owner()));

grant select, insert, update on public.story_analysis, public.story_thread,
  public.story_thread_update, public.news_theme_day, public.news_brief, public.news_feedback
  to app_backend;
create policy backend_all on public.story_analysis
  for all to app_backend using (true) with check (true);
create policy backend_all on public.story_thread
  for all to app_backend using (true) with check (true);
create policy backend_all on public.story_thread_update
  for all to app_backend using (true) with check (true);
create policy backend_all on public.news_theme_day
  for all to app_backend using (true) with check (true);
create policy backend_all on public.news_brief
  for all to app_backend using (true) with check (true);
create policy backend_all on public.news_feedback
  for all to app_backend using (true) with check (true);
