drop policy if exists screen_run_owner_select on public.screen_run;
grant insert, update, delete on public.screen_run to authenticated;
create policy screen_run_owner_all on public.screen_run for all to authenticated
  using (owner_id = (select auth.uid())) with check (owner_id = (select auth.uid()));
