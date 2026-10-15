-- For a copy made before restore.sh created the auth.users stub: add the owner foreign keys
-- the restore could not (23 tables, owner_id -> auth.users(id) on delete cascade).
create table if not exists auth.users (id uuid primary key);
insert into auth.users (id) values ('14d3aa1a-624e-4be4-95b5-5aad6f6fe688') on conflict do nothing;
do $f$
declare t text;
begin
  foreach t in array array[
    'company_case_item','company_peer_group','company_report_version','company_segment_map',
    'company_valuation_scenario','decision_journal','feature_activation','fund_comparison_saved',
    'fund_nav_point','fund_owner_fact','performance_setting','pilot_defect','pilot_run',
    'quality_snooze','saved_analysis','screen_column_set','screen_definition','screen_run',
    'source_audit','switch_log','usage_event','watchlist_item','xray_owner_input']
  loop
    if not exists (select 1 from pg_constraint where conname = t || '_owner_id_fkey') then
      execute format('alter table public.%I add constraint %I foreign key (owner_id)'
                     ' references auth.users(id) on delete cascade', t, t || '_owner_id_fkey');
    end if;
  end loop;
end $f$;
