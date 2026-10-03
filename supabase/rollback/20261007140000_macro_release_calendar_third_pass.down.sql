-- Rollback for supabase/migrations/20261007140000_macro_release_calendar_third_pass.sql (plan D8.4).
-- Proven by apps/backend/tests/test_lifecycle_phase8.py: the migration is applied, this file is
-- run, and the catalog fingerprint (tables, columns, constraints, indexes, functions, policies,
-- grants, seed rows) must equal the one taken before the migration. Run it in a transaction,
-- newest migration first. The migration file itself is never edited.
-- Also proven by apps/backend/tests/test_macro_release_calendar_third_pass.py (data round trip of
-- the property_prices handling).
-- DATA LOSS: deletes the stored published_rule rows (the macro context then falls back to the
-- observed-cadence estimate for euro_area_10y_yield and czeonia), the zone columns with their
-- values (the zone is derivable again from the publisher), and the 'bls' release_calendar
-- provider_access / manifest rows with their fetch-audit, quota-usage and coverage rows; the BLS
-- dates stored under provider 'fred' (calendar key 'bls:%') are deleted too (FRED's date-only rows
-- stay). Reversed exactly: the property_prices re-key (rows come back to series_id
-- property_prices unless a same-keyed row already exists, in which case they stay re-keyed) and
-- the supersede of the BIS CPI-category calendar rows (superseded_at is cleared).

alter table public.macro_release_calendar disable trigger append_only;
delete from public.macro_release_calendar
 where source = 'published_rule' or calendar_key like 'bls:%';
update public.macro_release_calendar set superseded_at = null
 where provider = 'bis' and series_id = 'property_prices' and calendar_key = 'CPI'
   and superseded_at is not null;
alter table public.macro_release_calendar enable trigger append_only;

update public.macro_observation m
   set series_id = 'property_prices', metadata = m.metadata - 'rekeyed_from'
 where m.source = 'bis' and m.series_id = 'bis_long_cpi'
   and m.metadata->>'rekeyed_from' = 'property_prices'
   and not exists (select 1 from public.macro_observation o
                    where o.series_id = 'property_prices' and o.source = m.source
                      and o.geography = m.geography and o.period = m.period
                      and o.vintage = m.vintage and o.revision = m.revision);
delete from public.macro_series_metadata
 where source = 'bis' and series_id = 'bis_long_cpi'
   and consumer = 'none: quarantined data, not used by any product feature'
   and not exists (select 1 from public.macro_observation
                    where source = 'bis' and series_id = 'bis_long_cpi');

delete from public.coverage_status where provider = 'macro_release_calendar' and dataset = 'bls';
delete from public.provider_fetch_audit where endpoint = 'release_calendar' and provider = 'bls';
delete from public.provider_quota_usage where endpoint = 'release_calendar' and provider = 'bls';
delete from public.provider_access where endpoint = 'release_calendar' and provider = 'bls';  -- the manifest row cascades

alter table public.macro_release_calendar
  drop constraint macro_release_calendar_zone_needs_time_check,
  drop constraint macro_release_calendar_zone_pair_check,
  drop constraint macro_release_calendar_zone_source_check,
  drop column release_timezone_source,
  drop column release_timezone;
alter table public.macro_release_calendar drop constraint macro_release_calendar_source_check;
alter table public.macro_release_calendar
  add constraint macro_release_calendar_source_check check (source = 'provider_calendar');
