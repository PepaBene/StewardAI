-- Test-only stand-in for the parts of a Supabase database the migrations rely on, so
-- they can be applied to a plain Postgres. Mirrors Supabase's roles, auth.uid(), the
-- default privileges it gives anon/authenticated on public tables, and one pre-existing
-- table the backend was never granted.

do $$
begin
  if not exists (select from pg_roles where rolname = 'anon') then
    create role anon nologin noinherit;
  end if;
  if not exists (select from pg_roles where rolname = 'authenticated') then
    create role authenticated nologin noinherit;
  end if;
  if not exists (select from pg_roles where rolname = 'service_role') then
    create role service_role nologin noinherit bypassrls;
  end if;
end
$$;

create schema auth;
grant usage on schema auth to anon, authenticated, service_role;

create table auth.users (id uuid primary key, email text);

-- Same resolution order as Supabase's auth.uid().
create function auth.uid() returns uuid
language sql stable
as $$
  select coalesce(
    nullif(current_setting('request.jwt.claim.sub', true), ''),
    (nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub')
  )::uuid
$$;

grant usage on schema public to anon, authenticated, service_role;
alter default privileges in schema public grant all on tables to anon, authenticated, service_role;
alter default privileges in schema public grant all on sequences to anon, authenticated, service_role;
alter default privileges in schema public grant all on functions to anon, authenticated, service_role;

-- A table the frontend can reach through its own policy but app_backend was never granted.
create table public.frontend_only (id int primary key, note text);
alter table public.frontend_only enable row level security;
create policy anon_read on public.frontend_only for select to anon using (true);
insert into public.frontend_only values (1, 'not for the backend');
