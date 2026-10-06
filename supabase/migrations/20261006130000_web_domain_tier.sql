-- Source tiers the AI assigned to web domains that the gap-research lists do not name.
-- The AI can only place a domain in tier 2 or 3 (never tier 1, never a denied domain); the
-- owner can change any row and an owner decision is never overwritten by a later AI run.

create table public.web_domain_tier (
  domain text primary key check (domain = lower(domain) and domain !~ '[/:\s]'),
  tier text not null check (tier in ('T2', 'T3', 'rejected')),
  decided_by text not null,
  confidence numeric check (confidence between 0 and 1),
  reasons text not null default '',
  decided_at timestamptz not null default now()
);

alter table public.web_domain_tier enable row level security;
revoke all on public.web_domain_tier from anon, authenticated;
grant select on public.web_domain_tier to authenticated;
grant select, insert, update, delete on public.web_domain_tier to app_backend;
create policy owner_read on public.web_domain_tier for select to authenticated
  using ((select private.is_owner()));
create policy backend_all on public.web_domain_tier for all to app_backend
  using (true) with check (true);
