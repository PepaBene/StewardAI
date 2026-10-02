-- Run in the Supabase SQL editor on the live project, ONE transaction (all or nothing).
-- Identical to supabase/migrations/20261005160000_free_source_alert_gates.sql, wrapped in begin/commit.
begin;

-- Phase 8 gate enforcement for the free-source alerts: a decision the verification gates
-- blocked is recorded in the trail as `blocked_by_gate`. Such a row is provisional: the
-- evaluator replaces it by the real outcome once the gate opens (it never counts as "seen").
-- Rebuilt from the live constraint: every earlier value is kept.

alter table public.free_source_alert_trail drop constraint free_source_alert_trail_outcome_check;
alter table public.free_source_alert_trail add constraint free_source_alert_trail_outcome_check
  check (outcome in ('raised', 'throttled', 'revision_ignored', 'baseline', 'not_material',
    'no_baseline', 'persisting', 'review_needed', 'below_severity', 'run_cap',
    'blocked_by_gate'));

commit;
