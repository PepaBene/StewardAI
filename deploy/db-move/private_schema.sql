-- The `private` schema, exported by hand: the app_backend role (the only login the copy has)
-- cannot read it, so pg_dump leaves it out. It holds the trigger guards and private.is_owner()
-- that the public tables, triggers and 207 policies depend on. Applied before the public restore.
create schema if not exists private;
grant usage on schema private to authenticated, app_backend;

create table if not exists private.app_owner (
  user_id uuid primary key,
  created_at timestamptz not null default now()
);
create unique index if not exists app_owner_single_row on private.app_owner ((true));
insert into private.app_owner (user_id, created_at)
values ('14d3aa1a-624e-4be4-95b5-5aad6f6fe688', '2026-09-25 15:42:20.40498+00')
on conflict do nothing;

CREATE OR REPLACE FUNCTION private.config_version_guard()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO ''
AS $function$
begin
  if tg_op = 'DELETE' then
    raise exception 'config_version rows are immutable and cannot be deleted';
  end if;
  if (new.domain, new.body, new.schema_version, new.parent_id, new.diff, new.note,
      new.created_by, new.created_at, new.id)
     is distinct from
     (old.domain, old.body, old.schema_version, old.parent_id, old.diff, old.note,
      old.created_by, old.created_at, old.id) then
    raise exception 'config_version rows are immutable; only "active" may change';
  end if;
  return new;
end;
$function$;

CREATE OR REPLACE FUNCTION private.fact_guard()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO ''
AS $function$
begin
  if tg_op = 'DELETE' then
    raise exception 'fact rows cannot be deleted; quarantine them instead';
  end if;
  if (to_jsonb(new) - array['status', 'status_reason', 'reviewed_at', 'reviewed_by', 'value_units'])
     is distinct from
     (to_jsonb(old) - array['status', 'status_reason', 'reviewed_at', 'reviewed_by', 'value_units']) then
    raise exception 'fact rows are immutable; only status, status_reason and review fields may change';
  end if;
  return new;
end;
$function$;

CREATE OR REPLACE FUNCTION private.is_owner()
 RETURNS boolean
 LANGUAGE sql
 STABLE SECURITY DEFINER
 SET search_path TO ''
AS $function$
  select exists (select 1 from private.app_owner where user_id = (select auth.uid()));
$function$;

CREATE OR REPLACE FUNCTION private.report_escalation_status_default()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO ''
AS $function$
begin
  if new.type = 'escalation' and new.escalation_status is null then
    new.escalation_status := 'open';
  end if;
  return new;
end;
$function$;

CREATE OR REPLACE FUNCTION private.run_retention()
 RETURNS TABLE(tbl text, deleted integer)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog', 'public'
AS $function$
declare
  p record;  -- Supabase has public.retention_policy%rowtype; that table does not exist yet here
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
end $function$;

CREATE OR REPLACE FUNCTION private.screen_version_immutable()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO ''
AS $function$
begin
  raise exception 'screen_definition_version rows are immutable';
end $function$;

CREATE OR REPLACE FUNCTION private.set_updated_at()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO ''
AS $function$
begin
  new.updated_at := now();
  return new;
end;
$function$;

-- Same grants as on Supabase: execute only for the roles that use them.
revoke all on function private.is_owner() from public;
grant execute on function private.is_owner() to authenticated;
revoke all on function private.run_retention() from public, anon, authenticated;
grant execute on function private.run_retention() to app_backend;
