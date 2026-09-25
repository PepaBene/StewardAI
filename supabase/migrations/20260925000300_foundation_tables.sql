-- Foundation tables: versioned config and the instrument registry.
--
-- Access model, the same for every table in this project:
--   * RLS is enabled. The frontend (role `authenticated`) may only SELECT, and only when
--     the signed-in user is the owner (private.is_owner()). `anon` gets nothing.
--   * Writes from the frontend go through the Python API, which validates them first.
--   * `app_backend` gets explicit table grants plus a permissive policy: RLS guards the
--     frontend's path, not the backend's.

-- ---------------------------------------------------------------------------
-- Shared trigger: maintain updated_at
-- ---------------------------------------------------------------------------
create function private.set_updated_at()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  new.updated_at := now();
  return new;
end;
$$;

-- ---------------------------------------------------------------------------
-- config_version: every preference, versioned. Rows are immutable except for the
-- `active` flag; a change is a new row, so old verdicts stay tied to the version
-- they were judged under.
-- ---------------------------------------------------------------------------
create table public.config_version (
  id             bigint generated always as identity primary key,
  domain         text        not null,
  body           jsonb       not null,
  schema_version integer     not null check (schema_version > 0),
  parent_id      bigint      references public.config_version (id),
  diff           jsonb,
  note           text,
  created_by     uuid,
  created_at     timestamptz not null default now(),
  active         boolean     not null default false,
  constraint config_version_domain_known check (domain in (
    'strategy', 'theses', 'holding_profiles', 'news_sources', 'event_rules',
    'notifications', 'research_profiles', 'verification', 'jobs', 'budget'
  )),
  constraint config_version_body_is_object check (jsonb_typeof(body) = 'object')
);

comment on table public.config_version is
  'Versioned config per domain. Validated by the backend''s Pydantic schema before insert.';
comment on column public.config_version.diff is
  'Diff against parent_id (the previously active version of the same domain); null for the first.';

-- At most one active version per domain.
create unique index config_version_one_active_per_domain
  on public.config_version (domain) where active;
create index config_version_domain_created_at
  on public.config_version (domain, created_at desc);

create function private.config_version_guard()
returns trigger
language plpgsql
set search_path = ''
as $$
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
$$;

create trigger config_version_immutable
  before update or delete on public.config_version
  for each row execute function private.config_version_guard();

-- ---------------------------------------------------------------------------
-- instrument: one row per Trading212 instrument the app knows about.
-- ---------------------------------------------------------------------------
create table public.instrument (
  t212_ticker        text primary key,
  isin               text check (isin ~ '^[A-Z]{2}[A-Z0-9]{9}[0-9]$'),
  name               text,
  short_name         text,
  instrument_type    text,
  exchange           text,
  currency           text check (currency ~ '^[A-Za-z]{3}$'),
  cik                text check (cik ~ '^[0-9]{10}$'),
  next_report_date   date,
  report_date_source text,
  created_at         timestamptz not null default now(),
  updated_at         timestamptz not null default now()
);

comment on column public.instrument.currency is
  'Trading currency as Trading212 reports it; may be a minor unit such as GBX (pence).';
comment on column public.instrument.cik is 'SEC Central Index Key, zero-padded to 10 digits (US issuers).';
comment on column public.instrument.report_date_source is 'URL the next_report_date was taken from.';

create index instrument_isin on public.instrument (isin);

create trigger instrument_set_updated_at
  before update on public.instrument
  for each row execute function private.set_updated_at();

-- ---------------------------------------------------------------------------
-- Privileges and row-level security
-- ---------------------------------------------------------------------------
alter table public.config_version enable row level security;
alter table public.instrument enable row level security;

-- Supabase grants anon/authenticated broad default privileges on public tables;
-- narrow them to what the frontend actually needs.
revoke all on public.config_version, public.instrument from anon, authenticated;
grant select on public.config_version, public.instrument to authenticated;

create policy owner_read on public.config_version
  for select to authenticated using ((select private.is_owner()));
create policy owner_read on public.instrument
  for select to authenticated using ((select private.is_owner()));

-- Backend: config versions are inserted and (de)activated, never deleted.
grant select, insert, update on public.config_version to app_backend;
grant select, insert, update, delete on public.instrument to app_backend;

create policy backend_all on public.config_version
  for all to app_backend using (true) with check (true);
create policy backend_all on public.instrument
  for all to app_backend using (true) with check (true);
