-- Two autopilot fixes.
--
-- An escalation keeps its first `decided_at` (the clock the expiry rule reads) however often
-- the judge looks at it again; `last_judged_at` is when it last looked, which is what decides
-- whether the item is asked about again yet.
alter table public.auto_decision add column last_judged_at timestamptz;
comment on column public.auto_decision.last_judged_at is
  'When the judge last looked at an escalation; decided_at stays at the first escalation.';
update public.auto_decision set last_judged_at = decided_at where status = 'escalated';

-- The learned floor is a float4: 0.99::real is above the double 0.99, so the old bounds check
-- refused the highest floor the autopilot can learn. Compare as numeric (6 digits) instead.
alter table public.autopilot_learned drop constraint autopilot_learned_min_confidence_check;
alter table public.autopilot_learned add constraint autopilot_learned_min_confidence_check
  check (min_confidence::numeric between 0.5 and 0.99);
