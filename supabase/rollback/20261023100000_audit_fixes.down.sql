-- Rollback for supabase/migrations/20261023100000_audit_fixes.sql.
-- Run it in a transaction, newest migration first. The migration file itself is never edited.
-- DATA LOSS: drops the saved result of every valuation scenario version saved after the migration.
-- Restores Supabase's default grants to the signed-in role and its insert policies.
-- Proven by apps/backend/tests/test_lifecycle_phase8.py (needs STEWARD_TEST_DATABASE_URL).

alter table public.company_valuation_scenario drop column if exists result;

grant delete on public.company_valuation_scenario to app_backend;

do $$
declare t text;
begin
  foreach t in array array[
    'decision_journal', 'feature_activation', 'source_audit', 'pilot_run', 'usage_event',
    'quality_snooze', 'pilot_defect', 'company_report_version', 'saved_analysis',
    'company_valuation_scenario'
  ] loop
    execute format('grant all on public.%I to authenticated', t);
  end loop;
end $$;

create policy decision_journal_owner_insert on public.decision_journal for insert to authenticated
  with check (owner_id = (select auth.uid()));
create policy feature_activation_owner_insert on public.feature_activation for insert to authenticated
  with check (owner_id = (select auth.uid()));
create policy source_audit_owner_insert on public.source_audit for insert to authenticated
  with check (owner_id = (select auth.uid()));
create policy pilot_run_owner_insert on public.pilot_run for insert to authenticated
  with check (owner_id = (select auth.uid()));
create policy usage_event_owner_insert on public.usage_event for insert to authenticated
  with check (owner_id = (select auth.uid()));
create policy quality_snooze_owner_insert on public.quality_snooze for insert to authenticated
  with check (owner_id = (select auth.uid()));
create policy pilot_defect_owner_insert on public.pilot_defect for insert to authenticated
  with check (owner_id = (select auth.uid()));
