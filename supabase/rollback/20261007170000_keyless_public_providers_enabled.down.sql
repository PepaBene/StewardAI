-- Rollback for supabase/migrations/20261007170000_keyless_public_providers_enabled.sql (plan D8.4).
-- Proven by apps/backend/tests/test_browser_pass_providers.py: a jobs config with the three
-- providers disabled is saved, the migration is applied (a new active version enables them), this
-- file is run, and the original version is the active one again (and the provider_access detail
-- text is back); the catalog fingerprint test (test_lifecycle_phase8.py) covers the gate rows.
-- Run it in a transaction, newest migration first. The migration file itself is never edited.
-- config_version rows are immutable and never deleted, so the version the migration added stays
-- in the history as an inactive row; nothing is lost. A newer version saved after the migration
-- is left active (the original is not reactivated over it).

update public.config_version set active = false
 where domain = 'jobs' and active and note like 'keyless-public-providers-enabled:%';

update public.config_version p set active = true
  from public.config_version a
 where a.domain = 'jobs' and a.note like 'keyless-public-providers-enabled:%'
   and a.parent_id = p.id
   and not exists (select 1 from public.config_version o where o.domain = 'jobs' and o.active);

update public.provider_access
   set detail = replace(detail, '; collected by default (jobs config on)', '')
 where ((provider = 'world_bank' and endpoint = 'indicators')
        or (provider in ('oecd', 'bis') and endpoint = 'sdmx'))
   and detail like '%; collected by default (jobs config on)';
