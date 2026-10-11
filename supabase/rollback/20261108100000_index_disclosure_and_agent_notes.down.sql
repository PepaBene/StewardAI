-- Rollback for supabase/migrations/20261108100000_index_disclosure_and_agent_notes.sql.
-- Run it in a transaction, newest migration first. The migration file itself is never edited.
-- DATA LOSS: the agent notes' history is dropped (the notes themselves stay), indexed passages
-- of kind 'disclosure' and 'agent_note' are deleted, and inbox items triggered by a disclosure
-- alert, a breach or a verdict are deleted, because the narrower checks would reject them. The
-- passages are derived data (the next index run rebuilds them under the previous code, which
-- skips them); the inbox items can be regenerated from their triggers, but their feedback and
-- the note history cannot.
-- Proven by apps/backend/tests/test_lifecycle_phase8.py (needs STEWARD_TEST_DATABASE_URL).
drop table if exists public.agent_note_version;

delete from public.document_chunk where kind in ('disclosure', 'agent_note');
delete from public.inbox_item where trigger_kind in ('disclosure', 'breach', 'verdict');

alter table public.inbox_item drop constraint inbox_item_trigger_kind_check;
alter table public.inbox_item add constraint inbox_item_trigger_kind_check
  check (trigger_kind in ('escalation', 'alert'));

alter table public.document_chunk drop constraint document_chunk_kind_check;
alter table public.document_chunk add constraint document_chunk_kind_check
  check (kind in ('filing', 'report', 'thesis', 'note', 'news'));
