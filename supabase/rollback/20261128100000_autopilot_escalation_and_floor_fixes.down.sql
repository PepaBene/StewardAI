alter table public.autopilot_learned drop constraint autopilot_learned_min_confidence_check;
alter table public.autopilot_learned add constraint autopilot_learned_min_confidence_check
  check (min_confidence between 0.5 and 0.99);
alter table public.auto_decision drop column last_judged_at;
