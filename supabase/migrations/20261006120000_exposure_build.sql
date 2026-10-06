-- Exposure builder: geography weights computed from reported XBRL country revenue.
-- Built rows are marked so they can be refreshed without touching the owner's own entries, and
-- every (issuer, geography) ever built is logged so a row the owner deleted is not rebuilt.

alter table public.exposure_mapping
  add column origin text not null default 'user' check (origin in ('user', 'built')),
  add column provenance jsonb not null default '{}';

create table public.exposure_build_log (
  issuer_id bigint not null references public.issuer(id),
  geography text not null,
  first_built_at timestamptz not null default now(),
  primary key (issuer_id, geography)
);

alter table public.exposure_build_log enable row level security;
revoke all on public.exposure_build_log from anon, authenticated;
grant select on public.exposure_build_log to authenticated;
grant select, insert, update, delete on public.exposure_build_log to app_backend;
create policy owner_read on public.exposure_build_log for select to authenticated
  using ((select private.is_owner()));
create policy backend_all on public.exposure_build_log for all to app_backend
  using (true) with check (true);
