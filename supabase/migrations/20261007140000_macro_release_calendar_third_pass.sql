-- Third pass of the macro release calendar (docs/data/macro-release-calendar.md).
--   1. Basis of a stored date: `source` widens from 'provider_calendar' to also admit
--      'published_rule' (a date derived from a release rule AND a holiday list that the provider
--      itself publishes: ECB yield curves "every TARGET business day" with the T2 closing days,
--      ČNB CZEONIA "next working day" with the ČNB bank holiday list).
--   2. Time zone of a published release time: release_timezone (IANA name) and
--      release_timezone_source ('stated_by_provider' | 'agency_location'). A zone exists only for a
--      row that has a release_time. Existing rows with a time are backfilled from the publisher's
--      seat (ECB Frankfurt, ČNB Prague, DG ECFIN Brussels): none of those pages states a zone, so
--      the source is 'agency_location', an assumption the API shows as such.
--   3. The BLS release schedule (iCal, 08:30 US Eastern) as its own keyless endpoint 'bls'; its
--      rows are stored under provider 'fred' (the observation source), so the provider check is
--      unchanged.
--   4. property_prices defect: the BIS series labelled "Residential property price index" was
--      configured on dataflow WS_LONG_CPI (consumer prices). Non-destructive, reversible handling:
--        * observations stored under series_id property_prices that came from WS_LONG_CPI are
--          RE-KEYED to series_id bis_long_cpi (lossless: every column is kept, the old id is
--          recorded in metadata.rekeyed_from), so nothing mislabelled stays readable as property
--          prices while the consumer-price data is kept under its right name;
--        * the calendar rows that mapped property_prices to the BIS CPI category are superseded
--          (the append-only mechanism: superseded_at, rows are never deleted or rewritten).
--      Rows from WS_SPP (the right flow) are untouched.
alter table public.macro_release_calendar drop constraint macro_release_calendar_source_check;
alter table public.macro_release_calendar
  add constraint macro_release_calendar_source_check
  check (source in ('provider_calendar','published_rule'));

alter table public.macro_release_calendar
  add column release_timezone text,
  add column release_timezone_source text;

-- the table is append-only (trigger): lift it for this one deliberate, deterministic backfill
alter table public.macro_release_calendar disable trigger append_only;
update public.macro_release_calendar
   set release_timezone = case provider
         when 'ecb' then 'Europe/Berlin'
         when 'cnb' then 'Europe/Prague'
         when 'eurostat' then 'Europe/Brussels'  -- the timed eurostat rows are DG ECFIN's
       end,
       release_timezone_source = 'agency_location'
 where release_time is not null and provider in ('ecb','cnb','eurostat');
alter table public.macro_release_calendar enable trigger append_only;

alter table public.macro_release_calendar
  add constraint macro_release_calendar_zone_source_check
  check (release_timezone_source is null
         or release_timezone_source in ('stated_by_provider','agency_location')),
  add constraint macro_release_calendar_zone_pair_check
  check ((release_timezone is null) = (release_timezone_source is null)),
  add constraint macro_release_calendar_zone_needs_time_check
  check (release_timezone is null or release_time is not null);

insert into public.provider_access
  (provider,endpoint,state,credential_env,quota,quota_period,request_weight,detail,hard_blocked,tested_at,last_success_at)
values
 ('bls','release_calendar','available',null,null,null,1,'keyless BLS release schedule iCalendar; needs a User-Agent with a contact address (verified 2026-10-03)',false,'2026-10-03T20:00:00Z','2026-10-03T20:00:00Z')
on conflict (provider, endpoint) do nothing;
insert into public.provider_endpoint_manifest(provider,endpoint,path_template,response_unit,production_consumer)
values
 ('bls','release_calendar','/schedule/news_release/bls.ics','release dates and times','macro context next_release (us_cpi, us_unemployment)')
on conflict (provider, endpoint) do nothing;

-- property_prices: re-key the observations that came from the consumer-price dataflow
update public.macro_observation
   set series_id = 'bis_long_cpi',
       metadata = metadata || '{"rekeyed_from":"property_prices"}'::jsonb
 where source = 'bis' and series_id = 'property_prices'
   and (source_url like '%WS_LONG_CPI%'
        or (not (metadata->'dimensions' ? 'VALUE') and source_url not like '%WS_SPP%'));
insert into public.macro_series_metadata
  (source,series_id,label,definition,frequency,unit,consumer)
select 'bis','bis_long_cpi','BIS consumer prices (WS_LONG_CPI), re-keyed',
       'Consumer prices statistics (BIS WS_LONG_CPI). These rows were stored under series_id '
       'property_prices by a configuration defect (the series was labelled residential property '
       'prices); migration 20261007140000 re-keyed them. Unit and frequency are as stored on the rows.',
       (select frequency from public.macro_observation where source='bis' and series_id='bis_long_cpi' limit 1),
       (select unit from public.macro_observation where source='bis' and series_id='bis_long_cpi' limit 1),
       'none: quarantined data, not used by any product feature'
 where exists (select 1 from public.macro_observation
                where source='bis' and series_id='bis_long_cpi'
                  and metadata->>'rekeyed_from' = 'property_prices')
on conflict (source, series_id) do nothing;
-- the calendar rows that dated property_prices through the CPI category
update public.macro_release_calendar
   set superseded_at = greatest(known_at, now())
 where provider = 'bis' and series_id = 'property_prices' and calendar_key = 'CPI'
   and superseded_at is null;
