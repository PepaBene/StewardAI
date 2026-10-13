-- Rollback for supabase/migrations/20261115100000_chat_owner_actions.sql.
-- Run it in a transaction, newest migration first. The migration file itself is never edited.
-- DATA LOSS: chat actions that are owner actions (not jobs) are deleted so the old check holds.
-- Proven by apps/backend/tests/test_lifecycle_phase8.py (needs STEWARD_TEST_DATABASE_URL).
delete from public.chat_action
  where kind not in ('earnings_check', 'research', 'portfolio_sync');
alter table public.chat_action drop constraint chat_action_kind_check;
alter table public.chat_action
  add constraint chat_action_kind_check
  check (kind in ('earnings_check', 'research', 'portfolio_sync'));
