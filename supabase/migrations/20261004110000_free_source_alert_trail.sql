-- D7.5 free-source alerts: the owner-readable decision trail and the per-subject state the
-- evaluator compares against (patent legal status has no history of its own).

create table public.free_source_alert_trail (
  id bigint generated always as identity primary key,
  rule text not null check (rule in ('credit_stress', 'trade_exposure', 'patent_status',
    'company_graph')),
  dedupe_key text not null,
  scope text not null,
  ticker text,
  outcome text not null check (outcome in ('raised', 'throttled', 'revision_ignored',
    'baseline', 'not_material', 'no_baseline', 'persisting', 'review_needed',
    'below_severity', 'run_cap')),
  severity smallint check (severity between 0 and 3),
  reason text not null,
  evidence jsonb not null default '{}',
  news_item_id bigint references public.news_item (id) on delete set null,
  job_id bigint references public.job (id) on delete set null,
  config_version_id bigint references public.config_version (id) on delete set null,
  created_at timestamptz not null default now(),
  unique (rule, dedupe_key)
);
create index free_source_alert_trail_recent on public.free_source_alert_trail (created_at desc);
create index free_source_alert_trail_scope
  on public.free_source_alert_trail (rule, scope, outcome, created_at desc);
create index free_source_alert_trail_news_item on public.free_source_alert_trail (news_item_id);
create index free_source_alert_trail_job on public.free_source_alert_trail (job_id);
create index free_source_alert_trail_config_version
  on public.free_source_alert_trail (config_version_id);

create table public.free_source_alert_state (
  rule text not null,
  subject_key text not null,
  state text not null,
  updated_at timestamptz not null default now(),
  primary key (rule, subject_key)
);

do $$ declare t text; begin foreach t in array array[
  'free_source_alert_trail', 'free_source_alert_state'] loop
  execute format('alter table public.%I enable row level security', t);
  execute format('revoke all on public.%I from anon, authenticated', t);
  execute format('grant select on public.%I to authenticated', t);
  execute format('grant select, insert, update, delete on public.%I to app_backend', t);
  execute format('create policy owner_read on public.%I for select to authenticated using ((select private.is_owner()))', t);
  execute format('create policy backend_all on public.%I for all to app_backend using (true) with check (true)', t);
end loop; end $$;
grant usage, select on all sequences in schema public to app_backend;
