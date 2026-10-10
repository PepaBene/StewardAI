-- Covering indexes for four foreign keys that had none, so deleting a parent row (or a cascade
-- from auth.users) does not scan the child table.

create index if not exists company_case_item_owner_idx
  on public.company_case_item (owner_id);
create index if not exists company_report_version_research_report_idx
  on public.company_report_version (research_report_id);
create index if not exists screen_run_screen_idx
  on public.screen_run (screen_id);
create index if not exists screen_run_universe_snapshot_idx
  on public.screen_run (universe_snapshot_id);
