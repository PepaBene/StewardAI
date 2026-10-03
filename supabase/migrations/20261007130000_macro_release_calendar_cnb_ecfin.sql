-- Second pass of the macro release calendar (docs/data/macro-release-calendar.md): two more free,
-- official, machine-readable calendars.
--   * cnb   : the ČNB Bank Board monetary-policy meeting dates (series cnb_repo_2w); its rows are
--             stored with provider 'cnb' (the observation source), so the provider check admits it.
--   * ecfin : the DG ECFIN business and consumer survey "Publication dates" PDF (date and time of
--             the Economic Sentiment Indicator). It is called and gated as its own endpoint
--             ('ecfin'); the rows it yields date the 'eurostat' sentiment series, so no 'ecfin'
--             value is ever stored in macro_release_calendar.
-- The ECB Governing Council meeting calendar (series ecb_deposit_rate) is read by the existing
-- 'ecb' endpoint, and the Eurostat / BIS mappings were corrected in code; neither needs schema.
-- Additive: the rows already stored stay valid.
alter table public.macro_release_calendar
  drop constraint macro_release_calendar_provider_check;
alter table public.macro_release_calendar
  add constraint macro_release_calendar_provider_check
  check (provider in ('fred','ecb','eurostat','bis','cnb'));

-- Endpoint gates: free and keyless, verified live on 2026-10-03 (a real request succeeded, hence
-- last_success_at; see provider_state_latch 20261005100000). Neither publishes a request limit, so
-- the collector's own daily cap is the hard stop (CALENDAR_BUDGETS).
insert into public.provider_access
  (provider,endpoint,state,credential_env,quota,quota_period,request_weight,detail,hard_blocked,tested_at,last_success_at)
values
 ('ecfin','release_calendar','available',null,null,null,1,'keyless DG ECFIN business and consumer survey publication-date PDF (verified 2026-10-03)',false,'2026-10-03T18:00:00Z','2026-10-03T18:00:00Z'),
 ('cnb','release_calendar','available',null,null,null,1,'keyless CNB Bank Board decisions page, monetary-policy meeting dates (verified 2026-10-03)',false,'2026-10-03T18:00:00Z','2026-10-03T18:00:00Z')
on conflict (provider, endpoint) do nothing;

insert into public.provider_endpoint_manifest(provider,endpoint,path_template,response_unit,production_consumer)
values
 ('ecfin','release_calendar','/economy_finance/db_indicators/surveys/documents/calendar/Publication dates {year}.pdf','release dates','macro context next_release (sentiment)'),
 ('cnb','release_calendar','/en/monetary-policy/bank-board-decisions/','release dates','macro context next_release (cnb_repo_2w)')
on conflict (provider, endpoint) do nothing;
