-- Rollback for supabase/migrations/20261001115301_free_sources_fk_indexes.sql (plan D8.4).
-- Generated from a before/after diff of the public catalog and proven by
-- apps/backend/tests/test_lifecycle_phase8.py: the migration is applied, this file is run, and the
-- catalog fingerprint (tables, columns, constraints, indexes, functions, policies, grants, seed
-- rows) must equal the one taken before the migration. Run it in a transaction, newest migration
-- first. The migration file itself is never edited.
-- No row data is lost; only schema objects and seed rows the migration added are removed.

drop index if exists public.company_event_evidence;
drop index if exists public.company_event_issuer;
drop index if exists public.company_graph_evidence;
drop index if exists public.company_graph_object_issuer;
drop index if exists public.company_graph_subject_issuer;
drop index if exists public.corporate_action_evidence;
drop index if exists public.corporate_action_successor_listing;
drop index if exists public.exchange_session_source_evidence;
drop index if exists public.financial_fact_amends;
drop index if exists public.financial_fact_evidence;
drop index if exists public.financial_fact_issuer;
drop index if exists public.forward_observation_evidence;
drop index if exists public.issuer_ir_release_document;
drop index if exists public.patent_family_owner_evidence;
drop index if exists public.patent_family_owner_issuer;
drop index if exists public.peer_set_evidence;
drop index if exists public.peer_set_issuer;
drop index if exists public.relationship_disclosure_evidence;
drop index if exists public.security_listing_successor;
