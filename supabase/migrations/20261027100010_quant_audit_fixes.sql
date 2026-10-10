-- Fixes from the 10 October 2026 quant audit (docs/revision/quant_risk_audit_2026-10-10.md).
--
-- R5: a saved risk report was keyed on its data date (the last Wednesday of the weekly grid), so
-- the first report of a week won and every later one, after a trade, a deposit or a price move,
-- was dropped. The key now includes a hash of the report, so each different report is kept and
-- an identical one is still not written twice. Rows saved before this have the hash 'legacy'.
--
-- R8: the switch log is written only through the API (journal/switches.py checks the date, looks
-- up the prices and keeps the ranking snapshot), like the other append-only tables
-- (20261023100000_audit_fixes.sql). The signed-in role only reads it.

alter table public.risk_report_snapshot add column payload_hash text not null default 'legacy';
alter table public.risk_report_snapshot alter column payload_hash drop default;
alter table public.risk_report_snapshot
  drop constraint risk_report_snapshot_kind_scope_as_of_method_version_key;
alter table public.risk_report_snapshot
  add constraint risk_report_snapshot_report_key
  unique (kind, scope, as_of, method_version, payload_hash);

drop policy if exists switch_log_owner_insert on public.switch_log;
revoke insert on public.switch_log from authenticated;
