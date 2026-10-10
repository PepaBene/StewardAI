-- Rollback for supabase/migrations/20261027100010_quant_audit_fixes.sql.
-- Run it in a transaction, newest migration first. The migration file itself is never edited.
-- DATA LOSS: keeps one saved report per kind, scope, data date and method version (the oldest);
-- the later ones are deleted.
-- Proven by apps/backend/tests/test_lifecycle_phase8.py (needs STEWARD_TEST_DATABASE_URL).

delete from public.risk_report_snapshot a
  using public.risk_report_snapshot b
  where a.kind = b.kind and a.scope = b.scope and a.as_of = b.as_of
    and a.method_version = b.method_version and a.id > b.id;
alter table public.risk_report_snapshot drop constraint risk_report_snapshot_report_key;
alter table public.risk_report_snapshot drop column payload_hash;
alter table public.risk_report_snapshot
  add constraint risk_report_snapshot_kind_scope_as_of_method_version_key
  unique (kind, scope, as_of, method_version);

grant insert on public.switch_log to authenticated;
create policy switch_log_owner_insert on public.switch_log for insert to authenticated
  with check (owner_id = (select auth.uid()));
