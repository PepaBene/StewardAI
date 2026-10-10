-- investment_universe.has_prices and history_from matched price_bar.symbol to the instrument's
-- Stooq-style price_symbol ('nvda.us'), but the stored bars are Yahoo-style ('NVDA'), so every
-- holding showed no price history (Discover: "0 with price history").
--
-- The bars carry the listing they belong to, so a bar of the instrument's listing counts as well.
-- The view keeps its columns and their order; only the two price columns widen.

create index if not exists price_bar_listing_date on public.price_bar (listing_id, date)
  where listing_id is not null;

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
