-- Browser-pass item 8: public_data_collect skipped World Bank, OECD and BIS with "provider
-- disabled or access gate not available".  The access gate was right (source_audit and the seeded
-- provider_access rows say 'available' for world_bank/indicators, oecd/sdmx, bis/sdmx); the
-- jobs config's `providers.<name>.enabled` flag defaulted to false for every provider, including
-- these three keyless public-statistics APIs that have no account for an audit to qualify.  The
-- backend default is now true for them; a jobs config saved earlier carries the old default
-- explicitly, so this adds a new active jobs version that flips those three flags (and nothing
-- else).  Additive: a no-op when no jobs config has been saved or none of the three is false.

with changed as (
  select v.id as parent_id,
         v.schema_version,
         jsonb_set(jsonb_set(jsonb_set(v.body,
           '{providers,world_bank,enabled}', 'true', false),
           '{providers,oecd,enabled}', 'true', false),
           '{providers,bis,enabled}', 'true', false) as body
    from public.config_version v
   where v.domain = 'jobs' and v.active
     and (v.body #>> '{providers,world_bank,enabled}' = 'false'
          or v.body #>> '{providers,oecd,enabled}' = 'false'
          or v.body #>> '{providers,bis,enabled}' = 'false')
), retired as (
  update public.config_version v set active = false
    from changed c where v.id = c.parent_id
  returning v.id
)
insert into public.config_version
  (domain, body, schema_version, parent_id, diff, note, active)
select 'jobs', c.body, c.schema_version, c.parent_id,
       '[{"path":"/providers/{world_bank,oecd,bis}/enabled","op":"replace","old":false,"new":true}]'::jsonb,
       'keyless-public-providers-enabled: world_bank, oecd and bis default to on (migration 20261007170000)',
       true
  from changed c join retired r on r.id = c.parent_id;

-- The gate rows say what the default now is.
update public.provider_access set detail = detail || ' - collected by default (jobs config on)'
 where ((provider = 'world_bank' and endpoint = 'indicators')
        or (provider in ('oecd', 'bis') and endpoint = 'sdmx'))
   and detail like 'keyless public%' and detail not like '%collected by default%';
