-- Phase 8 (D8.2, D8.3, D8.6): verification evidence for the free data sources.
--   source_shadow_snapshot / source_shadow_job_snapshot: one row per provider (job type) and
--       day of the 30-day shadow collection; the sign-off report is computed from these.
--   source_shadow_report: generated sign-off reports (markdown + JSON) kept for audit.
--   domain_error_threshold: versioned domain-specific error thresholds (D8.3).
--   reconciliation_sample: sampled stored values compared with supplied primary-source values.
--   signal_audit_result: sampled evidence precision and point-in-time correctness per derived
--       signal (D8.6); `publish_gate` reads the latest result.
-- Nothing here stores a figure the app presents as data: every number is a measurement of the
-- collection itself, and an empty table means "not measured", never zero.

create table public.source_shadow_snapshot (
  snapshot_date date not null,
  provider text not null,
  observations_new integer not null default 0 check (observations_new >= 0),
  metrics_covered integer not null default 0 check (metrics_covered >= 0),
  entities_covered integer not null default 0 check (entities_covered >= 0),
  unique_observations integer not null default 0 check (unique_observations >= 0),
  conflicts_opened integer not null default 0 check (conflicts_opened >= 0),
  false_matches integer not null default 0 check (false_matches >= 0),
  identity_decisions integer not null default 0 check (identity_decisions >= 0),
  fetch_attempts integer not null default 0 check (fetch_attempts >= 0),
  fetch_failures integer not null default 0 check (fetch_failures >= 0),
  denials integer not null default 0 check (denials >= 0),
  credits_used numeric not null default 0 check (credits_used >= 0),
  quota_limit numeric check (quota_limit is null or quota_limit >= 0),
  quota_period text,
  newest_known_at timestamptz,
  freshness_hours numeric check (freshness_hours is null or freshness_hours >= 0),
  coverage_available integer not null default 0 check (coverage_available >= 0),
  coverage_total integer not null default 0 check (coverage_total >= 0),
  detail jsonb not null default '{}',
  recorded_at timestamptz not null default now(),
  primary key (snapshot_date, provider)
);
create index source_shadow_snapshot_provider on public.source_shadow_snapshot(provider, snapshot_date desc);

create table public.source_shadow_job_snapshot (
  snapshot_date date not null,
  job_type text not null,
  runs integer not null default 0 check (runs >= 0),
  failed integer not null default 0 check (failed >= 0),
  recorded_at timestamptz not null default now(),
  primary key (snapshot_date, job_type),
  check (failed <= runs)
);

create table public.source_shadow_report (
  id bigint generated always as identity primary key,
  generated_at timestamptz not null default now(),
  window_start date not null,
  window_end date not null,
  days_observed integer not null check (days_observed >= 0),
  sign_off_ready boolean not null,
  report jsonb not null,
  markdown text not null,
  check (window_end >= window_start)
);
create index source_shadow_report_recent on public.source_shadow_report(generated_at desc);

create table public.domain_error_threshold (
  domain text not null check (domain in
    ('prices','corporate_actions','statements','fund_weights','macro','patents')),
  version text not null,
  params jsonb not null check (jsonb_typeof(params) = 'object'),
  active boolean not null default false,
  note text,
  created_at timestamptz not null default now(),
  primary key (domain, version)
);
create unique index domain_error_threshold_one_active
  on public.domain_error_threshold(domain) where active;

create table public.reconciliation_sample (
  id bigint generated always as identity primary key,
  domain text not null check (domain in
    ('prices','corporate_actions','statements','fund_weights','macro','patents')),
  subject_key text not null,
  metric text not null,
  period_end date,
  stored_value numeric,
  primary_value numeric,
  primary_source text not null,
  primary_url text,
  abs_error numeric check (abs_error is null or abs_error >= 0),
  rel_error numeric check (rel_error is null or rel_error >= 0),
  matched boolean not null,
  failure_reason text,
  threshold_version text not null,
  reviewer text,
  sampled_at timestamptz not null default now()
);
create index reconciliation_sample_domain on public.reconciliation_sample(domain, sampled_at desc);

create table public.signal_audit_result (
  id bigint generated always as identity primary key,
  signal_name text not null,
  jurisdiction text,
  metric text,
  sample_size integer not null check (sample_size >= 0),
  evidence_correct integer not null check (evidence_correct >= 0),
  pit_checked integer not null check (pit_checked >= 0),
  pit_violations integer not null check (pit_violations >= 0),
  reviewer text,
  details jsonb not null default '{}',
  audited_at timestamptz not null default now(),
  check (evidence_correct <= sample_size),
  check (pit_violations <= pit_checked)
);
create index signal_audit_result_signal on public.signal_audit_result(signal_name, audited_at desc);

do $$ declare t text; begin foreach t in array array[
  'source_shadow_snapshot','source_shadow_job_snapshot','source_shadow_report',
  'domain_error_threshold','reconciliation_sample','signal_audit_result'
] loop
 execute format('alter table public.%I enable row level security', t);
 execute format('revoke all on public.%I from anon, authenticated', t);
 execute format('grant select on public.%I to authenticated', t);
 execute format('grant select, insert, update, delete on public.%I to app_backend', t);
 execute format('create policy owner_read on public.%I for select to authenticated using ((select private.is_owner()))', t);
 execute format('create policy backend_all on public.%I for all to app_backend using (true) with check (true)', t);
end loop; end $$;
grant usage, select on all sequences in schema public to app_backend;

comment on table public.source_shadow_snapshot is
  'Daily per-provider measurements of the 30-day shadow collection (D8.2); no row = not measured.';
comment on table public.domain_error_threshold is
  'Versioned error thresholds per data domain (D8.3); built-in defaults apply when no active row exists.';
comment on table public.signal_audit_result is
  'Sampled evidence precision and point-in-time checks per derived signal (D8.6); read by publish_gate.';
