-- Rollback for supabase/migrations/20261005100200_lifecycle_evidence_integrity.sql (core stream, audit items listed there; proven by the test below).
-- Restoration proven by apps/backend/tests/test_lifecycle_phase8.py: the migration is applied, this file is run,
-- and the catalog fingerprint (tables, columns, constraints, indexes, functions, triggers,
-- policies, grants, views, seed rows) must equal the one taken before the migration. Run it in a
-- transaction, newest migration first. The migration file itself is never edited.
-- Not restorable: duplicate source_evidence rows that were merged into the surviving row (links
-- were repointed to it), and the known_at / valid_to_known_at / valid_to values written since.


drop index if exists public.fk_account_snapshot_config_version_id;
drop index if exists public.fk_account_snapshot_job_id;
drop index if exists public.fk_alert_breach_id;
drop index if exists public.fk_alert_cluster_id;
drop index if exists public.fk_alert_config_version_id;
drop index if exists public.fk_alert_job_id;
drop index if exists public.fk_alert_verdict_id;
drop index if exists public.fk_chat_action_job_id;
drop index if exists public.fk_chat_action_message_id;
drop index if exists public.fk_chat_message_job_id;
drop index if exists public.fk_config_version_parent_id;
drop index if exists public.fk_coverage_status_last_good_observation_id;
drop index if exists public.fk_etf_snapshot_job_id;
drop index if exists public.fk_eval_candidate_fact_id;
drop index if exists public.fk_eval_candidate_feedback_id;
drop index if exists public.fk_eval_candidate_news_item_id;
drop index if exists public.fk_evidence_job_id;
drop index if exists public.fk_exposure_mapping_evidence_id;
drop index if exists public.fk_exposure_mapping_issuer_id;
drop index if exists public.fk_fact_job_id;
drop index if exists public.fk_fact_restates_fact_id;
drop index if exists public.fk_fact_reconciliation_job_id;
drop index if exists public.fk_fact_review_corrected_fact_id;
drop index if exists public.fk_fact_review_job_id;
drop index if exists public.fk_filing_hit_news_item_id;
drop index if exists public.fk_filing_hit_relationship_id;
drop index if exists public.fk_form13f_dataset_job_id;
drop index if exists public.fk_form13f_filing_dataset_url;
drop index if exists public.fk_form13f_manager_dataset_dataset_url;
drop index if exists public.fk_fund_factsheet_snapshot_id;
drop index if exists public.fk_identity_match_decision_candidate_issuer_id;
drop index if exists public.fk_identity_match_decision_candidate_listing_id;
drop index if exists public.fk_identity_match_decision_candidate_security_id;
drop index if exists public.fk_identity_match_decision_instrument_ticker;
drop index if exists public.fk_identity_match_decision_provider_identifier_id;
drop index if exists public.fk_instrument_security_link_listing_id;
drop index if exists public.fk_instrument_security_link_security_id;
drop index if exists public.fk_issuer_alias_source_document_version_id;
drop index if exists public.fk_issuer_relationship_evidence_id;
drop index if exists public.fk_issuer_relationship_successor_id;
drop index if exists public.fk_news_item_job_id;
drop index if exists public.fk_news_item_source_id;
drop index if exists public.fk_nport_holding_held_ticker;
drop index if exists public.fk_nport_holding_issuer_id;
drop index if exists public.fk_nport_holding_security_id;
drop index if exists public.fk_portfolio_breach_config_version_id;
drop index if exists public.fk_portfolio_breach_first_job_id;
drop index if exists public.fk_portfolio_breach_last_job_id;
drop index if exists public.fk_position_snapshot_config_version_id;
drop index if exists public.fk_position_snapshot_job_id;
drop index if exists public.fk_provider_identifier_issuer_id;
drop index if exists public.fk_provider_identifier_listing_id;
drop index if exists public.fk_provider_identifier_security_id;
drop index if exists public.fk_report_job_id;
drop index if exists public.fk_security_issuer_id;
drop index if exists public.fk_shares_outstanding_job_id;
drop index if exists public.fk_source_conflict_alternative_observation_id;
drop index if exists public.fk_source_document_version_amends_version_id;
drop index if exists public.fk_source_document_version_superseded_by_id;
drop index if exists public.fk_source_evidence_document_version_id;
drop index if exists public.fk_source_evidence_observation_id;
drop index if exists public.fk_source_observation_issuer_id;
drop index if exists public.fk_source_observation_listing_id;
drop index if exists public.fk_source_observation_security_id;
drop index if exists public.fk_source_record_evidence_document_version_id;
drop index if exists public.fk_source_record_evidence_evidence_id;
drop index if exists public.fk_story_cluster_duplicate_of;
drop index if exists public.fk_transaction_job_id;
drop index if exists public.fk_verdict_config_version_id;
drop index if exists public.fk_verdict_job_id;
drop index if exists public.fk_xbrl_filing_job_id;

drop trigger append_only on public.source_document_version;
drop trigger append_only on public.source_evidence;
drop trigger append_only on public.source_record_evidence;
drop trigger append_only on public.listing_lifecycle_snapshot;
drop trigger append_only on public.legal_entity_snapshot;
drop trigger append_only on public.macro_payload_archive;
drop function public.forbid_append_only_mutation();
drop index public.source_evidence_version_quote;

alter table public.issuer_alias drop constraint issuer_alias_window_check;
alter table public.issuer_relationship drop constraint issuer_relationship_window_check;
comment on column public.issuer_alias.valid_to_known_at is null;
alter table public.issuer_alias drop column valid_to_known_at, drop column known_at;
alter table public.issuer_relationship drop column valid_to, drop column known_at;
alter table public.security_listing drop column known_at;
