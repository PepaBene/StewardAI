-- Rollback for supabase/migrations/20261028120000_universe_prices_by_profile_symbol.sql.
-- Run it in a transaction, newest migration first. The migration file itself is never edited.
-- No data is touched: the view returns to matching bars by price_symbol and listing only.
-- Proven by apps/backend/tests/test_lifecycle_phase8.py (needs STEWARD_TEST_DATABASE_URL).
create or replace view public.investment_universe with (security_invoker = true) as
select
  i.t212_ticker                              as ticker,
  i.name,
  i.isin,
  i.instrument_type                          as instrument_type,
  coalesce(s.kind, i.instrument_type)        as asset_type,
  l.id                                       as listing_id,
  s.id                                       as security_id,
  s.issuer_id,
  coalesce(l.mic, i.home_mic)                as mic,
  i.exchange,
  coalesce(iss.country, i.home_country)      as country,
  upper(coalesce(l.currency, i.currency))    as currency,
  coalesce(l.status, 'active')               as status,
  l.valid_from                               as active_from,
  l.valid_to                                 as active_to,
  lnk.status                                 as identity_status,
  i.price_symbol,
  exists (select 1 from public.financial_statement_fact f where f.ticker = i.t212_ticker)
                                             as has_statements,
  exists (select 1 from public.price_bar b
          where (i.price_symbol is not null and b.symbol = i.price_symbol)
             or (l.id is not null and b.listing_id = l.id))
                                             as has_prices,
  (select min(b.date) from public.price_bar b
   where (i.price_symbol is not null and b.symbol = i.price_symbol)
      or (l.id is not null and b.listing_id = l.id))
                                             as history_from
from public.instrument i
left join public.instrument_security_link lnk on lnk.ticker = i.t212_ticker
left join public.security s on s.id = lnk.security_id
left join public.security_listing l on l.id = lnk.listing_id
left join public.issuer iss on iss.id = s.issuer_id;
