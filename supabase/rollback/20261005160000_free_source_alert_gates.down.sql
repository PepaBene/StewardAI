-- Rollback for supabase/migrations/20261005160000_free_source_alert_gates.sql (plan D8.4).
-- Proven by apps/backend/tests/test_lifecycle_phase8.py: the migration is applied, this file is
-- run, and the catalog fingerprint (tables, columns, constraints, indexes, functions, policies,
-- grants, seed rows) must equal the one taken before the migration. Run it in a transaction,
-- newest migration first. The migration file itself is never edited.
-- DELETES the provisional `blocked_by_gate` trail rows (a blocked decision is re-evaluated on
-- every run and replaced by its real outcome once its gate opens); no other row is touched.

delete from public.free_source_alert_trail where outcome = 'blocked_by_gate';
alter table public.free_source_alert_trail drop constraint if exists free_source_alert_trail_outcome_check;
alter table public.free_source_alert_trail add constraint free_source_alert_trail_outcome_check CHECK ((outcome = ANY (ARRAY['raised'::text, 'throttled'::text, 'revision_ignored'::text, 'baseline'::text, 'not_material'::text, 'no_baseline'::text, 'persisting'::text, 'review_needed'::text, 'below_severity'::text, 'run_cap'::text])));
