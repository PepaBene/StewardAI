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
grant usage on schema auth, extensions to anon, authenticated, service_role, app_backend;
grant usage on schema public to anon, authenticated, service_role, app_backend;
SQL
psql_dst -c "alter database steward set search_path = public, extensions"

echo "== dump | restore"
START=$(date +%s)
pg_dump "$SRC_URL" --format=custom --no-owner --schema=public --schema=private \
  | pg_restore --dbname="$DST_URL" --no-owner 2>/tmp/restore.err
echo "restore finished in $(( $(date +%s) - START )) s; pg_restore wrote $(wc -l </tmp/restore.err) lines to stderr"
grep -v "already exists" /tmp/restore.err | head -40

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
