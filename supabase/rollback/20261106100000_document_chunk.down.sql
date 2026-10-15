-- Rollback for supabase/migrations/20261106100000_document_chunk.sql.
-- Run it in a transaction, newest migration first. The migration file itself is never edited.
-- DATA LOSS: the passage index and its embeddings (paid for) are dropped. They are derived
-- data, rebuilt by the document_index job at the cost of embedding again. The pgvector
-- extension is left installed (other tables may use it).
-- Proven by apps/backend/tests/test_lifecycle_phase8.py (needs STEWARD_TEST_DATABASE_URL).
drop table if exists public.document_chunk;
