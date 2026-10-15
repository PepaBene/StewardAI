#!/bin/sh
# Copy the Steward database from Supabase to the Railway Postgres service, over Railway's
# private network. Runs once in a throwaway service built from the `postgres:17` image.
#
# Environment (references to other services' variables, set on the throwaway service):
#   SRC_URL               Supabase connection string (session pooler), read-only use
#   DST_URL               the Railway Postgres connection string, as the superuser
#   APP_BACKEND_PASSWORD  password for the app_backend login role (shared with the backend)
#
# Safe to repeat: set WIPE=1 to drop and recreate the target schemas first.
# Only `public` and `private` are copied. Auth and Storage stay on Supabase, so the target
# gets the roles and the auth.uid()/auth.jwt()/auth.role() stubs the policies refer to.
set -u

psql_dst() { psql "$DST_URL" -X -q "$@"; }

echo "== roles"
# Roles may exist from an earlier run: errors here are expected and ignored.
psql_dst -c "create role anon nologin" 2>/dev/null || true
psql_dst -c "create role authenticated nologin" 2>/dev/null || true
psql_dst -c "create role service_role nologin bypassrls" 2>/dev/null || true
psql_dst -v pw="$APP_BACKEND_PASSWORD" 2>/dev/null <<'SQL' || true
create role app_backend login password :'pw';
SQL
psql_dst -v pw="$APP_BACKEND_PASSWORD" <<'SQL'
alter role app_backend login password :'pw';
SQL

if [ "${WIPE:-0}" = "1" ]; then
  echo "== wipe"
  # One table per statement: dropping the whole schema at once needs more locks than
  # max_locks_per_transaction allows on this small server.
  for t in $(psql_dst -At -c "select tablename from pg_tables where schemaname = 'public'"); do
    psql_dst -v ON_ERROR_STOP=1 -c "drop table if exists public.\"$t\" cascade" || exit 1
  done
  psql_dst -v ON_ERROR_STOP=1 -c "drop schema if exists public cascade; drop schema if exists private cascade; create schema public;"
fi

echo "== extensions and auth stubs"
psql_dst -v ON_ERROR_STOP=1 <<'SQL' || exit 1
create schema if not exists extensions;
create extension if not exists vector schema public;
create extension if not exists pgcrypto schema extensions;
create extension if not exists "uuid-ossp" schema extensions;
create schema if not exists auth;
create or replace function auth.uid() returns uuid language sql stable as
  $f$ select nullif(current_setting('request.jwt.claim.sub', true), '')::uuid $f$;
create or replace function auth.jwt() returns jsonb language sql stable as
  $f$ select coalesce(nullif(current_setting('request.jwt.claims', true), ''), '{}')::jsonb $f$;
create or replace function auth.role() returns text language sql stable as
  $f$ select nullif(current_setting('request.jwt.claim.role', true), '') $f$;
-- 23 public tables have owner_id references auth.users(id): keep them valid with a stub that
-- holds the single owner (Auth itself stays on Supabase).
create table if not exists auth.users (id uuid primary key);
insert into auth.users (id) values ('14d3aa1a-624e-4be4-95b5-5aad6f6fe688') on conflict do nothing;
grant usage on schema auth, extensions to anon, authenticated, service_role, app_backend;
grant usage on schema public to anon, authenticated, service_role, app_backend;
SQL
psql_dst -c "alter database steward set search_path = public, extensions"

echo "== private schema"
# app_backend cannot read `private`, so it is applied from a hand-made file (see its header).
psql_dst -v ON_ERROR_STOP=1 -f "$(dirname "$0")/private_schema.sql" || exit 1

echo "== dump | restore"
START=$(date +%s)
# Definitions, then the rows of one table at a time (a dropped connection to the pooler loses
# one table, which is retried, not the whole copy), then indexes and constraints.
: >/tmp/restore.err
pass() {
  pg_dump "$SRC_URL" --format=custom --no-owner --schema=public --section="$1" -f /tmp/pass.dump \
    && pg_restore --dbname="$DST_URL" --no-owner /tmp/pass.dump 2>>/tmp/restore.err
  rm -f /tmp/pass.dump
}
copy_table() {
  rm -f /tmp/table.dump
  # app_backend cannot bypass row-level security: read through its own policies (backend_all).
  pg_dump "$SRC_URL" --format=custom --no-owner --section=data --enable-row-security \
    -t "public.\"$1\"" -f /tmp/table.dump 2>>/tmp/restore.err || return 1
  pg_restore --dbname="$DST_URL" --no-owner --data-only /tmp/table.dump 2>>/tmp/restore.err \
    || { psql_dst -c "truncate public.\"$1\"" 2>/dev/null; return 1; }
}
pass pre-data || exit 1
echo "pre-data done after $(( $(date +%s) - START )) s"
failed_tables=""
for t in $(psql "$SRC_URL" -X -At -c "select tablename from pg_tables where schemaname='public' order by tablename"); do
  n=0
  until copy_table "$t"; do
    n=$((n + 1))
    if [ "$n" -ge 4 ]; then failed_tables="$failed_tables $t"; echo "GAVE UP on $t"; break; fi
    echo "retrying $t (attempt $((n + 1)))"; sleep 5
  done
done
rm -f /tmp/table.dump
echo "data done after $(( $(date +%s) - START )) s; failed tables:${failed_tables:- none}"
pass post-data || exit 1
echo "post-data done after $(( $(date +%s) - START )) s"
echo "restore finished in $(( $(date +%s) - START )) s; pg_restore wrote $(wc -l </tmp/restore.err) lines to stderr"
grep -v "already exists" /tmp/restore.err | head -40

echo "== sequences"
# app_backend may not read sequences, so their values are not copied: set each one past the
# largest value in the column it feeds.
psql_dst -v ON_ERROR_STOP=1 <<'SQL'
do $f$
declare r record; m bigint;
begin
  for r in
    select s.oid::regclass as seq, d.refobjid::regclass as tbl, a.attname as col
    from pg_class s
    join pg_depend d on d.objid = s.oid and d.classid = 'pg_class'::regclass and d.deptype in ('a', 'i')
    join pg_attribute a on a.attrelid = d.refobjid and a.attnum = d.refobjsubid
    where s.relkind = 'S' and s.relnamespace = 'public'::regnamespace
  loop
    execute format('select coalesce(max(%I), 0) from %s', r.col, r.tbl) into m;
    perform setval(r.seq, m + 1, false);
  end loop;
end $f$;
SQL

echo "== analyze"
psql_dst -c "analyze"

echo "== compare"
for t in job job_step source_record_evidence institutional_holding price_bar news_embedding document_chunk insider_transaction form13f_filing job_queue; do
  a=$(psql "$SRC_URL" -X -At -c "select count(*) from public.$t" 2>&1)
  b=$(psql "$DST_URL" -X -At -c "select count(*) from public.$t" 2>&1)
  echo "$t src=$a dst=$b"
done
echo "tables src=$(psql "$SRC_URL" -X -At -c "select count(*) from information_schema.tables where table_schema in ('public','private')") dst=$(psql "$DST_URL" -X -At -c "select count(*) from information_schema.tables where table_schema in ('public','private')")"
echo "policies src=$(psql "$SRC_URL" -X -At -c "select count(*) from pg_policies where schemaname='public'") dst=$(psql "$DST_URL" -X -At -c "select count(*) from pg_policies where schemaname='public'")"
echo "size dst=$(psql "$DST_URL" -X -At -c "select pg_size_pretty(pg_database_size(current_database()))")"
echo "== done"
