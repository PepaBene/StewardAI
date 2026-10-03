-- Release calendar of the macro providers: the dates each provider itself publishes for the next
-- release of a series (FRED/ALFRED release dates, Eurostat euro-indicator calendar, ECB
-- statistical calendar, BIS statistical release calendar). Read by the macro context as
-- `next_release` (basis `provider_calendar`); the observed-cadence estimate stays only a
-- fallback for series without a calendar row.
--
-- Append-only: a rescheduled release is a NEW row; the old row stays and gets `superseded_at`
-- (the only column that may change). A reader at `as_of` sees rows with known_at <= as_of that
-- were not yet superseded at that time.
create table public.macro_release_calendar (
  id bigint generated always as identity primary key,
  provider text not null check (provider in ('fred','ecb','eurostat','bis')),
  -- Our series key as stored in macro_observation.series_id (Eurostat: the key without the
  -- ':<geo>' suffix, e.g. 'hicp').
  series_id text not null check (series_id <> ''),
  -- The provider's own identifier of the calendar entry (FRED release id, Eurostat event title,
  -- ECB calendar page + dataset, BIS category id).
  calendar_key text not null check (calendar_key <> ''),
  release_date date not null,
  release_time time,  -- as published (local time of the publisher); null when only a date is given
  title text,
  reference_period text,
  source_url text not null check (source_url <> ''),
  source text not null default 'provider_calendar' check (source = 'provider_calendar'),
  retrieved_at timestamptz not null default now(),
  known_at timestamptz not null,
  superseded_at timestamptz,
  check (superseded_at is null or superseded_at >= known_at)
);
-- Natural key of a live entry. A date that was superseded and later published again is a new row.
create unique index macro_release_calendar_live
  on public.macro_release_calendar (provider, series_id, calendar_key, release_date)
  where superseded_at is null;
create index macro_release_calendar_lookup
  on public.macro_release_calendar (provider, series_id, release_date);

create trigger append_only before update or delete on public.macro_release_calendar
  for each row execute function public.forbid_append_only_mutation('superseded_at');

alter table public.macro_release_calendar enable row level security;
revoke all on public.macro_release_calendar from anon, authenticated;
grant select on public.macro_release_calendar to authenticated;
grant select, insert, update, delete on public.macro_release_calendar to app_backend;
create policy owner_read on public.macro_release_calendar for select to authenticated
  using ((select private.is_owner()));
create policy backend_all on public.macro_release_calendar for all to app_backend
  using (true) with check (true);

-- Endpoint gates: free, verified live on 2026-10-03 (a real request succeeded, hence
-- last_success_at; see provider_state_latch 20261005100000: available needs one) (see docs/data/macro-release-calendar.md).
-- Only FRED has a published limit (120 requests per minute for a free API key); the other
-- three publish none, so the collector's own daily cap is the hard stop.
insert into public.provider_access
  (provider,endpoint,state,credential_env,quota,quota_period,request_weight,detail,hard_blocked,tested_at,last_success_at)
values
 ('fred','release_calendar','available','FRED_API_KEY',120,'minute',1,'free API key: /fred/series/release + /fred/release/dates (verified 2026-10-03)',false,'2026-10-03T16:00:00Z','2026-10-03T16:00:00Z'),
 ('eurostat','release_calendar','available',null,null,null,1,'keyless iCalendar export of the release calendar (verified 2026-10-03)',false,'2026-10-03T16:00:00Z','2026-10-03T16:00:00Z'),
 ('ecb','release_calendar','available',null,null,null,1,'keyless statistical calendar pages (verified 2026-10-03)',false,'2026-10-03T16:00:00Z','2026-10-03T16:00:00Z'),
 ('bis','release_calendar','available',null,null,null,1,'keyless JSON release calendar of data.bis.org (verified 2026-10-03)',false,'2026-10-03T16:00:00Z','2026-10-03T16:00:00Z')
on conflict (provider, endpoint) do nothing;

insert into public.provider_endpoint_manifest(provider,endpoint,path_template,response_unit,production_consumer)
values
 ('fred','release_calendar','/fred/series/release; /fred/release/dates','release dates','macro context next_release'),
 ('eurostat','release_calendar','/eurostat/o/calendars/eventsIcal?category=2','release dates','macro context next_release'),
 ('ecb','release_calendar','/press/calendars/statscal/mfm/html/{page}.en.html','release dates','macro context next_release'),
 ('bis','release_calendar','/api/v0/releasecalendar','release dates','macro context next_release')
on conflict (provider, endpoint) do nothing;
