-- The Python backend's database role.
--
-- The backend connects directly to Postgres as `app_backend`, never as `postgres` or
-- `service_role`. It gets explicit grants per table in later migrations and nothing
-- else: no superuser, no BYPASSRLS, no default privileges on new tables.
--
-- The role is created without a password on purpose (no secret in git). After the
-- migration, set one out of band:
--   alter role app_backend with login password '<generated secret>';

do $$
begin
  if not exists (select from pg_catalog.pg_roles where rolname = 'app_backend') then
    create role app_backend nologin noinherit nosuperuser nocreatedb nocreaterole nobypassrls;
  end if;
end
$$;

comment on role app_backend is
  'StewardAI Python backend (API + worker). Least privilege: explicit per-table grants only.';

grant usage on schema public to app_backend;

-- Owner check used by row-level security policies for the frontend's direct reads.
-- It lives in a schema PostgREST does not expose, and is security definer so policies
-- can consult app_owner without granting the frontend any access to it.
create schema if not exists private;
revoke all on schema private from public;

create table private.app_owner (
  user_id    uuid primary key,
  created_at timestamptz not null default now()
);
-- Single-user app: at most one owner row.
create unique index app_owner_single_row on private.app_owner ((true));

comment on table private.app_owner is
  'The one Supabase Auth user allowed to read app data. Insert the owner''s auth.users id once.';

create function private.is_owner()
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select exists (select 1 from private.app_owner where user_id = (select auth.uid()));
$$;

revoke all on function private.is_owner() from public;
grant usage on schema private to authenticated;
grant execute on function private.is_owner() to authenticated;
