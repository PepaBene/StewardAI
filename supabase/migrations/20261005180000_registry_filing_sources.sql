-- Free-registry sources: Companies House (UK) and EDINET (Japan) filing adapters, and the
-- providers whose profile rows land in company_research_observation.
--
-- 1. provider_access + provider_endpoint_manifest rows for the new endpoints. Both providers
--    are keyed but free with no paid tier; the rows start `access_unverified` (the adapters'
--    first call is the probe) and are not hard-blocked. Hard stops come from the job
--    configuration's provider budgets (steward.verify_sources.budgets), so no quota is declared.
-- 2. company_research_observation.provider only allowed alpha_vantage, pdl, diffbot and epo_ops,
--    so the shipped FMP profile job could not store a row. Widened to add fmp (existing job)
--    and companies_house (profile fields); every earlier value is kept.
--
-- Twelve Data's `corporate_actions` (dividends) and `splits` endpoints are already seeded.

insert into public.provider_access
  (provider,endpoint,state,credential_env,quota,quota_period,request_weight,detail,hard_blocked)
values
 ('companies_house','profile','access_unverified','COMPANIES_HOUSE_API_KEY',null,null,1,'free key, first call is the probe',false),
 ('companies_house','filing_history','access_unverified','COMPANIES_HOUSE_API_KEY',null,null,1,'free key, first call is the probe',false),
 ('edinet','documents_list','access_unverified','EDINET_API_KEY',null,null,1,'free key, first call is the probe',false),
 ('edinet','document_download','access_unverified','EDINET_API_KEY',null,null,1,'free key, first call is the probe',false)
on conflict (provider,endpoint) do nothing;

insert into public.provider_endpoint_manifest
  (provider,endpoint,path_template,response_unit,request_weight,production_consumer)
values
 ('companies_house','profile','/company/{company_number}','profile fields',1,'UK issuer reference'),
 ('companies_house','filing_history','/company/{company_number}/filing-history','filing events',1,'UK filing events'),
 ('edinet','documents_list','/api/v2/documents.json?date={date}&type=2','document list',1,'Japan filing events'),
 ('edinet','document_download','/api/v2/documents/{docID}','document',1,'Japan filing documents')
on conflict (provider,endpoint) do nothing;

alter table public.company_research_observation
  drop constraint company_research_observation_provider_check;
alter table public.company_research_observation
  add constraint company_research_observation_provider_check
  check (provider in ('alpha_vantage','pdl','diffbot','epo_ops','fmp','companies_house'));
