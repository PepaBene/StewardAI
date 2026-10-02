-- Rollback for supabase/migrations/20261005100100_observation_identity_nulls.sql (core stream, audit items listed there; proven by the test below).
-- Restoration proven by apps/backend/tests/test_lifecycle_phase8.py: the migration is applied, this file is run,
-- and the catalog fingerprint (tables, columns, constraints, indexes, functions, triggers,
-- policies, grants, views, seed rows) must equal the one taken before the migration. Run it in a
-- transaction, newest migration first. The migration file itself is never edited.
-- DATA LOSS: the duplicate rows the migration removed (lowest id kept) are not restored.


drop index public.source_conflict_pair;
create unique index source_conflict_pair on public.source_conflict
  (preferred_observation_id,alternative_observation_id);

do $$
declare spec text[]; begin
  foreach spec slice 1 in array array[
    array['listing_lifecycle_snapshot','listing_lifecycle_snapshot_isin_mic_publication_date_conten_key',
          'isin, mic, publication_date, content_hash'],
    array['issuer_alias','issuer_alias_issuer_id_name_valid_from_key','issuer_id, name, valid_from'],
    array['security_listing','security_listing_security_id_mic_ticker_valid_from_key',
          'security_id, mic, ticker, valid_from'],
    array['provider_identifier','provider_identifier_provider_identifier_type_identifier_val_key',
          'provider, identifier_type, identifier, valid_from'],
    array['corporate_action','corporate_action_listing_id_action_type_ex_date_source_sour_key',
          'listing_id, action_type, ex_date, source, source_url'],
    array['filing_hit','filing_hit_ticker_kind_matched_term_accession_file_key',
          'ticker, kind, matched_term, accession, file']
  ] loop
    execute format('alter table public.%I drop constraint %I', spec[1], spec[2]);
    execute format('alter table public.%I add constraint %I unique (%s)', spec[1], spec[2], spec[3]);
  end loop;
end $$;

comment on index public.source_observation_identity is null;
drop index public.source_observation_identity;
create unique index source_observation_identity on public.source_observation
  (domain, metric, coalesce(issuer_id,0), coalesce(security_id,0), coalesce(listing_id,0),
   coalesce(geography,''), period_end, source, coalesce(vintage,''), revision);
