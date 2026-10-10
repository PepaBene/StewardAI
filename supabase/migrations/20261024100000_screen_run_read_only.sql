-- D2a: a saved screen run is reproducible evidence; the owner may read it through
-- PostgREST but only the backend writes or deletes it.
drop policy if exists screen_run_owner_all on public.screen_run;
revoke insert, update, delete on public.screen_run from authenticated;
create policy screen_run_owner_select on public.screen_run for select to authenticated
  using (owner_id = (select auth.uid()));
