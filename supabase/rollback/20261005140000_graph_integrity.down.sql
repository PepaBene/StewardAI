-- Rollback for supabase/migrations/20261005140000_graph_integrity.sql (plan D8.4).
-- Hand-written from the migration and proven by
-- apps/backend/tests/test_lifecycle_phase8.py: the migration is applied, this file is run, and the
-- catalog fingerprint (tables, columns, constraints, indexes, functions, triggers, policies, grants,
-- seed rows) must equal the one taken before the migration. Run it in a transaction, newest
-- migration first. The migration file itself is never edited.
-- DATA LOSS: drops these tables and every row in them: graph_rejection, graph_status_log, relationship_contradiction, patent_publication, wikidata_statement, graph_reconciliation, research_lookup_log, provider_auth_failure.
-- DATA LOSS: drops these columns and their values: company_graph_edge.object_key_norm, company_graph_edge.decided_by, company_event.decided_at, company_event.decision_reason, company_event.evidence_tier, company_event.deal_key, relationship_disclosure.counterparty_key_norm, relationship_disclosure.updated_at, relationship_disclosure.origin, filing_hit review columns (review_state, passage, passage_hash, fetched_at, review_note, attempts, disclosure_id, updated_at), patent_family.family_id_source, grant_date, expiry_date, legal_events, first_seen_status, patent_family_owner.path_edge_id, company_research_observation.last_seen_at.
-- NOT RESTORED: duplicate relationship_disclosure and company_research_observation rows the migration removed, and filing_hit rows relabelled 'relationship_candidate' return as 'supplier' (the keyword role the migration retired).

delete from public.provider_endpoint_manifest where provider = 'ted' and endpoint = 'notice_search';
delete from public.provider_access where provider = 'ted' and endpoint = 'notice_search';

drop table if exists public.graph_reconciliation cascade;
drop table if exists public.relationship_contradiction cascade;
drop table if exists public.wikidata_statement cascade;
drop table if exists public.patent_publication cascade;
drop table if exists public.research_lookup_log cascade;
drop table if exists public.provider_auth_failure cascade;
drop table if exists public.graph_status_log cascade;
drop table if exists public.graph_rejection cascade;

drop trigger if exists company_graph_edge_before on public.company_graph_edge;
drop trigger if exists company_graph_edge_after on public.company_graph_edge;
drop trigger if exists company_graph_edge_status_log on public.company_graph_edge;
drop trigger if exists company_event_status_log on public.company_event;
drop trigger if exists relationship_disclosure_before on public.relationship_disclosure;
drop trigger if exists relationship_disclosure_status_log on public.relationship_disclosure;
drop trigger if exists entity_relationship_graph_before on public.entity_relationship;
drop trigger if exists entity_relationship_graph_after on public.entity_relationship;
drop function if exists public.graph_edge_before();
drop function if exists public.graph_edge_after();
drop function if exists public.entity_relationship_before();
drop function if exists public.entity_relationship_after();
drop function if exists public.relationship_disclosure_before();
drop function if exists public.graph_log_status();
drop function if exists public.graph_status_as_of(text, bigint, timestamptz);
drop function if exists public.graph_record_rejection(text, text, text, text, text);
drop function if exists public.graph_canon_edge_type(text);
drop function if exists public.graph_norm_object_key(text);

drop index if exists public.company_research_observation_unique;
alter table public.company_research_observation drop column if exists last_seen_at cascade;

drop index if exists public.patent_family_owner_ticker;
drop index if exists public.patent_family_owner_path_edge;
alter table public.patent_family_owner drop column if exists path_edge_id cascade;
alter table public.patent_family
  drop column if exists family_id_source cascade,
  drop column if exists grant_date cascade,
  drop column if exists expiry_date cascade,
  drop column if exists legal_events cascade,
  drop column if exists first_seen_status cascade;

drop index if exists public.filing_hit_review;
drop index if exists public.filing_hit_disclosure;
alter table public.filing_hit
  drop column if exists review_state cascade,
  drop column if exists passage cascade,
  drop column if exists passage_hash cascade,
  drop column if exists fetched_at cascade,
  drop column if exists review_note cascade,
  drop column if exists attempts cascade,
  drop column if exists disclosure_id cascade,
  drop column if exists updated_at cascade;
update public.filing_hit set kind = 'supplier' where kind = 'relationship_candidate';
alter table public.filing_hit drop constraint if exists filing_hit_kind_check;
alter table public.filing_hit add constraint filing_hit_kind_check
  check (kind in ('topic','customer','supplier'));

drop index if exists public.relationship_disclosure_unique;
alter table public.relationship_disclosure
  drop column if exists counterparty_key_norm cascade,
  drop column if exists updated_at cascade,
  drop column if exists origin cascade;

drop index if exists public.company_event_deal;
alter table public.company_event drop constraint if exists company_event_confirmed_needs_evidence;
alter table public.company_event
  drop column if exists decided_at cascade,
  drop column if exists decision_reason cascade,
  drop column if exists evidence_tier cascade,
  drop column if exists deal_key cascade;

drop index if exists public.company_graph_edge_norm;
alter table public.company_graph_edge
  drop column if exists object_key_norm cascade,
  drop column if exists decided_by cascade;
