insert into public.provider_access(provider,endpoint,state,credential_env,quota,quota_period,detail)
values ('alpha_vantage','commodity','access_unverified','ALPHA_VANTAGE_API_KEY',25,'day','owner-key commodity-series probe'),
       ('alpha_vantage','fx_daily','access_unverified','ALPHA_VANTAGE_API_KEY',25,'day','owner-key FX daily probe')
on conflict(provider,endpoint) do nothing;
insert into public.provider_endpoint_manifest(provider,endpoint,path_template,response_unit,production_consumer)
values ('alpha_vantage','commodity','/query?function={WTI|BRENT|NATURAL_GAS|COPPER|ALUMINUM|WHEAT|CORN|COFFEE|SUGAR}','provider series units','configured input-cost exposure context'),
       ('alpha_vantage','fx_daily','/query?function=FX_DAILY','from/to currency pair','configured revenue-currency exposure context')
on conflict(provider,endpoint) do nothing;
