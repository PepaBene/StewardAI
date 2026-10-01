-- The Alpha Vantage 25-call daily limit is account-wide across all endpoints.
insert into public.provider_access
  (provider,endpoint,state,credential_env,quota,quota_period,detail,hard_blocked)
values ('alpha_vantage','account_free','available','ALPHA_VANTAGE_API_KEY',25,'day',
        'shared account-wide free-tier budget; reserved for existing Company Overview and audited free endpoints',false)
on conflict(provider,endpoint) do nothing;
insert into public.provider_endpoint_manifest
  (provider,endpoint,path_template,response_unit,request_weight,production_consumer)
values ('alpha_vantage','account_free','account-wide quota reservation','requests',1,
        'all Alpha Vantage endpoints share this daily budget')
on conflict(provider,endpoint) do nothing;
