-- Phase 6 (risk, scenarios and rebalancing decisions): saved scenarios and rebalance plans reuse
-- `saved_analysis`, so the allowed kinds grow. Rows are still never edited; a saved item is
-- archived, not deleted, and keeps the method version it was computed under.

alter table public.saved_analysis drop constraint saved_analysis_kind_check;
alter table public.saved_analysis add constraint saved_analysis_kind_check
  check (kind in (
    'xray_scenario', 'performance_report', 'shock_scenario', 'whatif_case', 'rebalance_plan',
    'theme_exposure'
  ));

comment on table public.saved_analysis is
  'Owner-saved X-Ray what-ifs, performance reports, shock scenarios, what-if cases and rebalance plans; archived, never deleted.';
