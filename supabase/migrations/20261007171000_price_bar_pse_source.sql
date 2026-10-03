-- Browser-pass item 11: the PX index (^px) had no daily price series without a Stooq key.  No free
-- keyless API carries PX history (Stooq wants a key; Yahoo's ^PX is an unrelated CBOE symbol;
-- PSE's own history table is loaded from /api/, which its robots.txt disallows; its FTP price
-- list holds issues, not indices), but the PSE index page (the existing adapter) states each
-- session's close.  The price job now stores that close as the day's bar, source 'pse', so the
-- series builds up one session at a time.  Additive: the source CHECK admits one more writer.

alter table public.price_bar drop constraint if exists price_bar_source_check;
alter table public.price_bar
  add constraint price_bar_source_check
  check (source in ('stooq', 'yahoo', 'eodhd', 'alpha_vantage', 'twelve_data', 'pse'));

comment on column public.price_bar.source is
  'stooq (source of record), yahoo (unofficial chart API), eodhd and alpha_vantage (gap '
  'fallbacks under free-tier budgets), twelve_data (reserved), pse (the Prague Stock Exchange '
  'index page, PX only, one close per session from the first day it was read).';
