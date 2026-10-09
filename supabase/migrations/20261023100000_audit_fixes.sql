-- Fixes from the 8 October 2026 audit (docs/revision/morningstar_audit_2026-10-08.md).
--
-- R3/R4: append-only and saved records are written only through the API (as app_backend),
-- which checks a review names the same security, holds a feature to the release gate and never
-- rewrites a saved result. The signed-in role kept Supabase's default grants (insert, update,
-- delete, truncate), so it could skip those rules through PostgREST. It now only reads.
--
-- R14: a saved valuation scenario keeps the result the owner saw, not only its assumptions.

do $$
declare t text;
begin
  foreach t in array array[
    'decision_journal', 'feature_activation', 'source_audit', 'pilot_run', 'usage_event',
    'quality_snooze', 'pilot_defect', 'company_report_version', 'saved_analysis',
    'company_valuation_scenario'
  ] loop
    execute format('revoke all on public.%I from authenticated', t);
    execute format('grant select on public.%I to authenticated', t);
  end loop;
end $$;

drop policy if exists decision_journal_owner_insert on public.decision_journal;
drop policy if exists feature_activation_owner_insert on public.feature_activation;
drop policy if exists source_audit_owner_insert on public.source_audit;
drop policy if exists pilot_run_owner_insert on public.pilot_run;
drop policy if exists usage_event_owner_insert on public.usage_event;
drop policy if exists quality_snooze_owner_insert on public.quality_snooze;
drop policy if exists pilot_defect_owner_insert on public.pilot_defect;

-- A saved scenario version is immutable (update trigger); it is not deleted either.
revoke delete on public.company_valuation_scenario from app_backend;

alter table public.company_valuation_scenario
  add column result jsonb check (result is null or jsonb_typeof(result) = 'object');

comment on column public.company_valuation_scenario.result is
  'The computed result as shown when the version was saved (with its method version); null for versions saved before 20261023100000.';
