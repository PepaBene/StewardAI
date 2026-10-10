-- Rollback for supabase/migrations/20261027100000_fk_covering_indexes.sql.
-- Run it in a transaction, newest migration first. The migration file itself is never edited.
-- No data is touched: the indexes only speed up reads and parent deletes.
-- Proven by apps/backend/tests/test_lifecycle_phase8.py (needs STEWARD_TEST_DATABASE_URL).
drop index if exists public.deal_document_document_version_id_idx;
drop index if exists public.deal_event_issuer_id_idx;
drop index if exists public.deal_participant_issuer_id_idx;
drop index if exists public.deal_state_transition_document_version_id_idx;
drop index if exists public.deal_state_transition_evidence_id_idx;
drop index if exists public.deal_structure_link_evidence_id_idx;
drop index if exists public.deal_structure_link_successor_issuer_id_idx;
drop index if exists public.deal_term_version_document_version_id_idx;
drop index if exists public.deal_term_version_evidence_id_idx;
drop index if exists public.deal_term_version_supersedes_id_idx;
drop index if exists public.pilot_defect_parent_id_idx;
drop index if exists public.public_estimate_snapshot_corrected_by_id_idx;
