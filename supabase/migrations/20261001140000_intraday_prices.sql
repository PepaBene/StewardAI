-- Prices from more than one source, intraday bars and index quotes.
--
--   price_bar       + source 'yahoo' (Yahoo Finance's unofficial chart API, daily bars) next to
--                   'stooq': Stooq stays the source of record; Yahoo fills a symbol Stooq
--                   refuses, cannot be reached for, or has no symbol for. A symbol keeps its
--                   own source's name (Stooq 'cez.cz', Yahoo 'CEZ.PR'), so the two never mix
--                   within one series. + currency: the quote currency the source reported
--                   (Yahoo's meta.currency); null for Stooq rows, whose currency follows from
--                   the symbol's market suffix.
--   intraday_bar    5-minute bars (Yahoo, ~15 min delayed on most exchanges), kept a few days
--                   (Config -> Jobs -> Prices) and pruned by the intraday job.
--   market_quote    the latest quote per symbol and source: an intraday last price with its
--                   trade time and exchange time zone (Yahoo's chart meta), or a close (the
--                   Prague Stock Exchange's PX page, read once a day). Each row points at the
--                   stored original payload it was read from.
--   market_payload  the latest original response per (source, symbol, kind), verbatim: the
--                   Yahoo chart JSON, the PSE detail page. What every intraday figure and
--                   non-Stooq close traces back to.
--
-- Same access model as every other table: RLS on, owner-only SELECT for the frontend,
-- `anon` gets nothing, `app_backend` gets explicit grants plus a permissive policy.

alter table public.price_bar
  add column currency text;

alter table public.price_bar
  add constraint price_bar_source_check check (source in ('stooq', 'yahoo'));

comment on column public.price_bar.source is
  'stooq (source of record) or yahoo (Yahoo Finance chart API, unofficial; the fallback).';
comment on column public.price_bar.currency is
  'Quote currency as the source reported it (Yahoo meta.currency, e.g. GBp for pence); null '
  'for Stooq rows (the symbol''s market suffix gives it).';

create table public.intraday_bar (
  symbol     text        not null,
  source     text        not null default 'yahoo' check (source in ('yahoo')),
  -- Start of the bar (UTC); the exchange time zone is on market_quote.
  ts         timestamptz not null,
  open       numeric,
  high       numeric,
  low        numeric,
  close      numeric     not null check (close > 0),
  volume     numeric,
  fetched_at timestamptz not null default now(),
  primary key (symbol, source, ts)
);

create index intraday_bar_ts on public.intraday_bar (ts);

create table public.market_payload (
  source       text        not null check (source in ('yahoo', 'pse')),
  symbol       text        not null,
  -- chart_5m (intraday), chart_1d (daily fallback), pse_detail (the PX detail page).
  kind         text        not null check (kind in ('chart_5m', 'chart_1d', 'pse_detail')),
  url          text        not null,
  http_status  integer     not null,
  content_type text,
  -- The response body exactly as received.
  body         text        not null,
  fetched_at   timestamptz not null default now(),
  primary key (source, symbol, kind)
);

create table public.market_quote (
  symbol          text        not null,
  source          text        not null check (source in ('yahoo', 'pse')),
  -- intraday: a last price during (or just after) the session; close: a session's close.
  kind            text        not null check (kind in ('intraday', 'close')),
  price           numeric     not null check (price > 0),
  -- When the price was traded or computed (UTC); `session_date` is its exchange-local day.
  price_time      timestamptz not null,
  session_date    date        not null,
  previous_close  numeric     check (previous_close > 0),
  currency        text,
  exchange_name   text,
  exchange_tz     text,
  session_start   timestamptz,
  session_end     timestamptz,
  payload_kind    text        not null check (payload_kind in ('chart_5m', 'chart_1d', 'pse_detail')),
  fetched_at      timestamptz not null default now(),
  primary key (symbol, source)
);

comment on table public.market_quote is
  'Latest quote per symbol and source; its original payload is market_payload '
  '(source, symbol, payload_kind).';

alter table public.intraday_bar enable row level security;
alter table public.market_payload enable row level security;
alter table public.market_quote enable row level security;

revoke all on public.intraday_bar, public.market_payload, public.market_quote
  from anon, authenticated;
grant select on public.intraday_bar, public.market_payload, public.market_quote
  to authenticated;

create policy owner_read on public.intraday_bar
  for select to authenticated using ((select private.is_owner()));
create policy owner_read on public.market_payload
  for select to authenticated using ((select private.is_owner()));
create policy owner_read on public.market_quote
  for select to authenticated using ((select private.is_owner()));

-- Intraday bars are pruned after a few days; payloads and quotes are replaced in place.
grant select, insert, update, delete on public.intraday_bar, public.market_payload,
  public.market_quote to app_backend;

create policy backend_all on public.intraday_bar
  for all to app_backend using (true) with check (true);
create policy backend_all on public.market_payload
  for all to app_backend using (true) with check (true);
create policy backend_all on public.market_quote
  for all to app_backend using (true) with check (true);
