-- Rollback for supabase/migrations/20261101100000_remove_claude_models.sql.
-- Nothing to undo: config versions are append-only, so the versions this migration replaced
-- are still in the history. Roll a config back from the Config history page if needed (a
-- version that names a claude-* model no longer passes validation).
-- Proven by apps/backend/tests/test_lifecycle_phase8.py (it only checks this file runs: the
-- migration changes data, not the schema).
select 1;
