select cron.unschedule('retention-nightly');
drop function if exists private.run_retention();
drop table if exists public.retention_policy;
create index if not exists source_record_evidence_asof
  on public.source_record_evidence (domain_table, record_key, known_at desc);
