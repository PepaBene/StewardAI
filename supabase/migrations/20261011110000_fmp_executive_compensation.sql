-- FMP executive compensation (plan M2.11): named-executive summary compensation from proxy
-- statements, read from /stable/governance-executive-compensation (free on the owner's key,
-- checked 7 October 2026). Rows land in company_research_observation (dataset
-- 'executive_compensation'); the endpoint gets its own provider_access and manifest rows so the
-- request ledger counts it. The free allowance of 250 requests a day is now shared by three
-- endpoints, so each is capped at a third in code (steward.sources.fmp.ENDPOINT_DAILY_CAP).

insert into public.provider_access
  (provider,endpoint,state,credential_env,quota,quota_period,request_weight,detail,hard_blocked)
values
 ('fmp','executive_compensation','access_unverified','FMP_API_KEY',250,'day',1,'owner-key endpoint probe',false)
on conflict (provider,endpoint) do nothing;

insert into public.provider_endpoint_manifest
  (provider,endpoint,path_template,response_unit,request_weight,production_consumer)
values
 ('fmp','executive_compensation','/stable/governance-executive-compensation','USD per fiscal year',1,'governance tab compensation table')
on conflict (provider,endpoint) do nothing;
