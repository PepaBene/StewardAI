-- Six holdings (NU, Chugai, Daiichi Sankyo, Brent, the commodity and Nasdaq ETFs) have stored
-- daily bars under a Yahoo symbol (BRNT.L, CUP.F, SXRV.DE ...) that neither the instrument's
-- Stooq-style price_symbol nor a listing link reaches, so Discover showed them without prices.
--
-- The holding profile names the Yahoo symbol (`yahoo_symbol`); where it does not, a US Stooq
-- symbol ('nu.us') is the bare ticker on Yahoo ('NU'), the same rule as
-- steward.reference.price_series.yahoo_from_stooq. The view keeps its columns and their order.

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
             or (l.id is not null and b.listing_id = l.id)
             or (y.symbol is not null and b.symbol = y.symbol))
                                             as has_prices,
  (select min(b.date) from public.price_bar b
   where (i.price_symbol is not null and b.symbol = i.price_symbol)
      or (l.id is not null and b.listing_id = l.id)
      or (y.symbol is not null and b.symbol = y.symbol))
                                             as history_from
from public.instrument i
left join public.instrument_security_link lnk on lnk.ticker = i.t212_ticker
left join public.security s on s.id = lnk.security_id
left join public.security_listing l on l.id = lnk.listing_id
left join public.issuer iss on iss.id = s.issuer_id
left join lateral (
  select coalesce(
    nullif(p.value->>'yahoo_symbol', ''),
    case when i.price_symbol ~ '\.us$' then upper(split_part(i.price_symbol, '.', 1)) end
  ) as symbol
  from (select 1) one
  left join (
    select e.value
    from public.config_version c, jsonb_array_elements(c.body->'profiles') e
    where c.domain = 'holding_profiles' and c.active
      and e.value->>'ticker' = i.t212_ticker
    limit 1
  ) p on true
) y on true;
