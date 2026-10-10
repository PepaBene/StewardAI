-- Owner settings for portfolio performance and holding-period tracking (plan Phase 4).
--
-- Append-only: the newest row per owner is the live setting, older rows stay so a saved result can
-- name the settings it was computed under. `settings` is validated by the backend
-- (steward.performance.settings.PerformanceSettings): core ETFs, cost-basis method, the
-- time-test length and annual exemption limit (owner-configured thresholds, not tax advice),
-- the sleeve gap and window, and the benchmark blend.

create table public.performance_setting (
  id bigint generated always as identity primary key,
  owner_id uuid not null references auth.users(id) on delete cascade,
  settings jsonb not null check (jsonb_typeof(settings) = 'object'),
  created_at timestamptz not null default now()
);
create index performance_setting_latest on public.performance_setting (owner_id, id desc);

alter table public.performance_setting enable row level security;
revoke all on public.performance_setting from anon;
grant select on public.performance_setting to authenticated;
grant select, insert on public.performance_setting to app_backend;
create policy performance_setting_owner on public.performance_setting for select to authenticated
  using (owner_id = (select auth.uid()));
create policy performance_setting_backend on public.performance_setting for all to app_backend
  using (true) with check (true);
grant usage, select on sequence public.performance_setting_id_seq to app_backend;

comment on table public.performance_setting is
  'Owner-set performance and holding-period settings; append-only, newest row wins.';
