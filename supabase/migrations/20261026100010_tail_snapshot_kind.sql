-- Q2.4: the tail-risk report (Expected Shortfall, drawdown odds, downside statistics) is saved
-- with its method version like the other risk reports, so a later change to the model cannot
-- rewrite what the owner saw. Adds the kind 'tail' to the saved-report table.

alter table public.risk_report_snapshot drop constraint risk_report_snapshot_kind_check;
alter table public.risk_report_snapshot add constraint risk_report_snapshot_kind_check
  check (kind in ('ex_ante', 'replay', 'factors', 'shock', 'tail'));
