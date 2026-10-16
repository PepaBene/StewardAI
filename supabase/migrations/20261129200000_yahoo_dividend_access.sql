-- Yahoo dividend events (insight I1.3) ship ahead of the rest of the insight work: the endpoint
-- the yahoo_dividends job calls through the provider gate. Seeded unverified, so the first
-- request is the probe. 20261130100000_insight_foundations.sql seeds the same rows and skips them.

insert into public.provider_access
  (provider,endpoint,state,credential_env,quota,quota_period,request_weight,detail,hard_blocked)
values
 ('yahoo','chart_dividends','access_unverified',null,null,null,1,'unofficial chart API, events=div; keyless probe',false)
on conflict (provider,endpoint) do nothing;

insert into public.provider_endpoint_manifest
  (provider,endpoint,path_template,response_unit,request_weight,production_consumer)
values
 ('yahoo','chart_dividends','/v8/finance/chart/{symbol}?events=div','dividend per share, listing currency',1,'second dividend source (suggested corporate actions)')
on conflict (provider,endpoint) do nothing;
