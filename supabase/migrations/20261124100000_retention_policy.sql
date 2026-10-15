-- Free database space on the Supabase free tier (0.5 GB): drop one redundant index and clear
-- operational logs once they are old. Source and domain data is never touched here.
--
-- source_record_evidence_asof (domain_table, record_key, known_at desc) is 72 MB and was scanned 35
-- times; the unique index on (domain_table, record_key, document_version_id) has the same leading
-- columns and serves the lookup, the few rows per record are sorted by known_at afterwards.
drop index if exists public.source_record_evidence_asof;

-- One row per table to trim. `ts_column` is the row's age; rows older than `keep_days` go, at
-- most `batch_limit` per run, so a large backlog is cleared over several nights, not in one lock.
create table public.retention_policy (
  table_name    text        primary key,
  ts_column     text        not null,
  keep_days     integer     not null check (keep_days >= 7),
  batch_limit   integer     not null default 20000 check (batch_limit > 0),
  enabled       boolean     not null default true,
  note          text,
  last_run_at   timestamptz,
  last_deleted  integer
);

alter table public.retention_policy enable row level security;
revoke all on public.retention_policy from anon, authenticated;
grant select on public.retention_policy to authenticated;
create policy owner_read on public.retention_policy
  for select to authenticated using ((select private.is_owner()));
grant select, insert, update on public.retention_policy to app_backend;
create policy backend_all on public.retention_policy
  for all to app_backend using (true) with check (true);

-- Logs and caches only: nothing here is a source of record or referenced by another table.
insert into public.retention_policy (table_name, ts_column, keep_days, note) values
  ('news_source_run',     'started_at',   60,  'per-fetch timings and drop counts'),
  ('provider_fetch_audit','attempted_at', 90,  'provider call audit'),
  ('graph_status_log',    'changed_at',   180, 'status transitions of graph rows'),
  ('ai_result_cache',     'created_at',   60,  'cached model results, recomputed on a miss');

create or replace function private.run_retention() returns table (tbl text, deleted integer)
language plpgsql security definer set search_path = pg_catalog, public as $$
declare
  p public.retention_policy%rowtype;
  n integer;
begin
  for p in select * from public.retention_policy where enabled order by table_name loop
    execute format(
      'delete from public.%1$I where ctid in (select ctid from public.%1$I'
      ' where %2$I < now() - make_interval(days => $1) limit $2)',
      p.table_name, p.ts_column)
      using p.keep_days, p.batch_limit;
    get diagnostics n = row_count;
    update public.retention_policy set last_run_at = now(), last_deleted = n
      where table_name = p.table_name;
    tbl := p.table_name; deleted := n; return next;
  end loop;
end $$;

revoke all on function private.run_retention() from public, anon, authenticated;
grant execute on function private.run_retention() to app_backend;

-- Scheduled only where pg_cron exists (Supabase); a plain test database skips it.
do $$
begin
  if exists (select 1 from pg_available_extensions where name = 'pg_cron') then
    create extension if not exists pg_cron;
    perform cron.schedule('retention-nightly', '17 3 * * *', 'select * from private.run_retention()');
  end if;
end $$;
