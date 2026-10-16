-- Insight plan I13.
--
-- 1. `feed_read` (I13.1): which typed-feed updates the owner has seen. Inbox items keep their own
--    `status`; every other update (news stories, what-changed items, dividend events) is marked
--    read here by its stable key, so unread counts on the filter chips are the owner's own.
-- 2. `earnings_note` (I13.5): the cited AI summary of a stored earnings release and the questions
--    to ask. Append-only: a new note for the same document is a new row, never an edit. Each
--    sentence carries the verbatim quote it rests on; sentences without one were dropped before
--    the row was written.

create table public.feed_read (
  owner_id uuid not null references auth.users(id) on delete cascade,
  item_key text not null check (length(item_key) between 3 and 200),
  read_at timestamptz not null default now(),
  primary key (owner_id, item_key)
);

create table public.earnings_note (
  id bigint generated always as identity primary key,
  owner_id uuid not null references auth.users(id) on delete cascade,
  ticker text not null check (length(trim(ticker)) between 1 and 40),
  evidence_sha256 text not null references public.evidence (sha256),
  source_url text,
  period text,
  summary jsonb not null,
  questions jsonb not null,
  dropped integer not null default 0 check (dropped >= 0),
  model text,
  method_version text not null,
  input_hash text not null,
  created_at timestamptz not null default now()
);
create index earnings_note_owner_ticker on public.earnings_note (owner_id, ticker, created_at desc);

alter table public.feed_read enable row level security;
revoke all on public.feed_read from anon;
grant select on public.feed_read to authenticated;
create policy feed_read_owner_read on public.feed_read for select to authenticated
  using (owner_id = (select auth.uid()));
create policy feed_read_backend on public.feed_read for all to app_backend using (true) with check (true);
grant select, insert, update, delete on public.feed_read to app_backend;

alter table public.earnings_note enable row level security;
revoke all on public.earnings_note from anon;
grant select on public.earnings_note to authenticated;
create policy earnings_note_owner_read on public.earnings_note for select to authenticated
  using (owner_id = (select auth.uid()));
create policy earnings_note_backend on public.earnings_note for all to app_backend using (true) with check (true);
grant select, insert on public.earnings_note to app_backend;
grant usage, select on sequence public.earnings_note_id_seq to app_backend;

comment on table public.feed_read is
  'Typed updates feed read state (insight I13.1) for everything but inbox items, which keep their own status.';
comment on table public.earnings_note is
  'Cited AI summary and questions for a stored earnings release (insight I13.5); every sentence has a verbatim quote.';
