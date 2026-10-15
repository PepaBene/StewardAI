-- Documents the index could not use (unreadable, too short, no passages). They are tried again
-- only after a while, so a few unreadable new filings no longer fill every run's quota and keep
-- older documents from ever being indexed.
create table public.document_index_skip (
  kind       text        not null,
  ref        text        not null,
  reason     text        not null check (char_length(reason) between 1 and 300),
  skipped_at timestamptz not null default now(),
  primary key (kind, ref)
);
comment on table public.document_index_skip is
  'Documents the document index could not use, with why; retried after a cool-down.';

alter table public.document_index_skip enable row level security;
revoke all on public.document_index_skip from anon, authenticated;
grant select, insert, update, delete on public.document_index_skip to app_backend;
create policy backend_all on public.document_index_skip
  for all to app_backend using (true) with check (true);
