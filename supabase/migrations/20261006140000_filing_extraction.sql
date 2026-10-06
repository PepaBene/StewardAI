-- One row per (holding, filing, kind of extraction) so an annual report is read by the AI once.
-- `detail` records what was proposed and stored; for exposures it also lists the keys written to
-- the profile, so a row the owner later deleted is recognised and never added back.

create table public.filing_extraction (
  ticker text not null,
  accession text not null,
  kind text not null check (kind in ('exposure', 'dependency', 'hs_mapping')),
  source_url text,
  extracted_at timestamptz not null default now(),
  detail jsonb not null default '{}',
  primary key (ticker, accession, kind)
);

alter table public.filing_extraction enable row level security;
revoke all on public.filing_extraction from anon, authenticated;
grant select on public.filing_extraction to authenticated;
grant select, insert, update, delete on public.filing_extraction to app_backend;
create policy owner_read on public.filing_extraction for select to authenticated
  using ((select private.is_owner()));
create policy backend_all on public.filing_extraction for all to app_backend
  using (true) with check (true);
