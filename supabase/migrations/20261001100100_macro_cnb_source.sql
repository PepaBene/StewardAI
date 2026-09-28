-- Czech National Bank (ČNB) series in public.macro_series: CZEONIA (the CZK overnight
-- reference rate, daily, from ČNB's keyless yearly.txt/daily.txt files; the risk-free rate
-- of the portfolio's Sharpe ratio) and the ČNB 2-week repo rate (its change history file;
-- a policy rate, so a new value is a central-bank decision). Only the source list changes.

alter table public.macro_series drop constraint macro_series_source_check;
alter table public.macro_series
  add constraint macro_series_source_check check (source in ('fred', 'ecb', 'eurostat', 'cnb'));
