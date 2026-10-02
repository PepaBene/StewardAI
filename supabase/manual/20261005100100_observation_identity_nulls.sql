-- Run in the Supabase SQL editor on the live project, ONE transaction (all or nothing).
-- Identical to supabase/migrations/20261005100100_observation_identity_nulls.sql, wrapped in begin/commit.
begin;

-- Audit items A8 and B6 (core stream).
--
-- A8: source_observation_identity omitted period_start, unit, currency and the dimensions, and
--     `revision` was never incremented, so restatements, segment variants and quarter-vs-YTD
--     rows that share a period_end were silently dropped.  The identity now is
--     (domain, metric, issuer/security/listing, geography, period_start, period_end, unit,
--     currency, dimensions hash, source, vintage, revision).  The new index is strictly finer
--     than the old one, so existing rows satisfy it.
-- B6: UNIQUE constraints over nullable columns treated NULLs as distinct, so `on conflict do
--     nothing` never deduplicated (listing_lifecycle_snapshot with no publication_date, a
--     corporate action without a listing, ...).  They become NULLS NOT DISTINCT (PostgreSQL 15+,
--     the Supabase baseline); the column lists, and so every `on conflict (...)` inference, are
--     unchanged.  Existing duplicates of unreferenced tables are removed first (lowest id kept).

drop index public.source_observation_identity;
create unique index source_observation_identity on public.source_observation
  (domain, metric, coalesce(issuer_id,0), coalesce(security_id,0), coalesce(listing_id,0),
   coalesce(geography,''), period_start, period_end, unit, coalesce(currency,''),
   md5(normalized::text), source, coalesce(vintage,''), revision);
comment on index public.source_observation_identity is
  'identity of an observation; normalized holds the dimensions; revision counts restatements';

delete from public.listing_lifecycle_snapshot d using public.listing_lifecycle_snapshot k
 where d.id > k.id and d.isin = k.isin and d.mic = k.mic and d.content_hash = k.content_hash
   and d.publication_date is not distinct from k.publication_date;
delete from public.issuer_alias d using public.issuer_alias k
 where d.id > k.id and d.issuer_id = k.issuer_id and d.name = k.name
   and d.valid_from is not distinct from k.valid_from;
delete from public.filing_hit d using public.filing_hit k
 where d.id > k.id and d.ticker = k.ticker and d.kind = k.kind and d.matched_term = k.matched_term
   and d.accession = k.accession and d.file is not distinct from k.file;
delete from public.corporate_action d using (
  select id, row_number() over (
           partition by listing_id, action_type, ex_date, source, source_url
           order by (status = 'suggested'), id) rn
    from public.corporate_action) r
 where d.id = r.id and r.rn > 1;
delete from public.source_conflict d using public.source_conflict k
 where d.id > k.id and d.alternative_observation_id = k.alternative_observation_id
   and d.preferred_observation_id is not distinct from k.preferred_observation_id;

-- security_listing and provider_identifier are referenced by other tables and have no writer
-- yet; if either held duplicates the statements below fail loudly rather than lose links.
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
    execute format('alter table public.%I add constraint %I unique nulls not distinct (%s)',
                   spec[1], spec[2], spec[3]);
  end loop;
end $$;

-- source_conflict_pair is a plain unique index (not a constraint).
drop index public.source_conflict_pair;
create unique index source_conflict_pair on public.source_conflict
  (preferred_observation_id, alternative_observation_id) nulls not distinct;

commit;
