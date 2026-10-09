-- Rollback for supabase/migrations/20261007100000_disclosure_foundation.sql.
-- Run it in a transaction, newest migration first. The migration file itself is never edited.
-- DATA LOSS: deletes every disclosure observation, extracted claim, coverage state, review
-- decision and audit event, the disclosure source registry, and the extra document/evidence
-- columns (first-seen time, archive path, parser version, quarantine flags, section/confidence).
-- Original documents, evidence quotes and the older domain tables are untouched.
-- Written by hand and not yet proven by apps/backend/tests/test_lifecycle_phase8.py, which needs
-- a database (STEWARD_TEST_DATABASE_URL); run that test before relying on this file.

drop table if exists public.quality_review_event;
drop table if exists public.quality_review;
drop table if exists public.disclosure_coverage;
drop table if exists public.extracted_claim;
drop table if exists public.disclosure_observation;
drop table if exists public.disclosure_entity_mapping;

drop index if exists public.source_document_version_known;
alter table public.security drop column if exists share_class;

alter table public.source_evidence
  drop column if exists section, drop column if exists row_ref,
  drop column if exists extraction_method, drop column if exists parser_version,
  drop column if exists confidence;

alter table public.source_document_version
  drop column if exists first_seen_at, drop column if exists byte_length,
  drop column if exists archive_path, drop column if exists date_notes,
  drop column if exists quarantined, drop column if exists quarantine_reason,
  drop column if exists parser_version;

alter table public.source_document
  drop column if exists issuer_id, drop column if exists form, drop column if exists fiscal_period,
  drop column if exists language, drop column if exists source_key,
  drop column if exists first_seen_at;

drop table if exists public.disclosure_source;
