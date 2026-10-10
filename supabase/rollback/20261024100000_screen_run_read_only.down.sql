-- Rollback for supabase/migrations/20261024100000_screen_run_read_only.sql.
-- Run it in a transaction, newest migration first. The migration file itself is never edited.
-- Restores the owner's insert/update/delete rights on screen_run (no data is touched).
-- Proven by apps/backend/tests/test_lifecycle_phase8.py (needs STEWARD_TEST_DATABASE_URL).
drop policy if exists screen_run_owner_select on public.screen_run;
grant insert, update, delete on public.screen_run to authenticated;
create policy screen_run_owner_all on public.screen_run for all to authenticated
  using (owner_id = (select auth.uid())) with check (owner_id = (select auth.uid()));
