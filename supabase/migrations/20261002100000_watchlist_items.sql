-- User-owned research watchlists, separate from Trading212 portfolio positions.
create table public.watchlist_item (
  id bigint generated always as identity primary key,
  owner_id uuid not null references auth.users(id) on delete cascade,
  ticker text not null check (ticker ~ '^[A-Za-z0-9._-]{1,32}$'),
  list_name text not null default 'Default' check (length(trim(list_name)) between 1 and 80),
  buy_target numeric check (buy_target is null or buy_target > 0),
  sell_target numeric check (sell_target is null or sell_target > 0),
  reason text check (reason is null or length(reason) <= 2000),
  note text check (note is null or length(note) <= 5000),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (owner_id, list_name, ticker)
);

create index watchlist_item_owner_list on public.watchlist_item (owner_id, list_name, ticker);
alter table public.watchlist_item enable row level security;
revoke all on public.watchlist_item from anon, authenticated;
grant select, insert, update, delete on public.watchlist_item to authenticated;
grant usage, select on sequence public.watchlist_item_id_seq to authenticated;
create policy watchlist_owner_all on public.watchlist_item
  for all to authenticated using (owner_id = (select auth.uid()))
  with check (owner_id = (select auth.uid()));
grant select, insert, update, delete on public.watchlist_item to app_backend;
create policy watchlist_backend_all on public.watchlist_item
  for all to app_backend using (true) with check (true);
