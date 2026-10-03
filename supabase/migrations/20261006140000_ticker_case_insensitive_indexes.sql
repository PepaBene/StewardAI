-- Case-insensitive ticker lookups for the PDMR and insider routes (audit F6-06).
--
-- T212 UK/EU tickers are stored with a lowercase exchange suffix ('RRl_EQ') while the routes used
-- to upper-case the requested ticker. The routes now compare lower(ticker) = lower($1); these
-- expression indexes keep that an index scan. No data is rewritten; the existing btree indexes
-- stay for exact-case callers. Tables keep their existing owner-only RLS and app_backend grants.

create index if not exists pdmr_notification_ticker_lower
  on public.pdmr_notification (lower(ticker), announced_at desc);
create index if not exists insider_filing_ticker_lower
  on public.insider_filing (lower(ticker));
create index if not exists insider_filing_ticker_link_lower
  on public.insider_filing_ticker (lower(ticker));
