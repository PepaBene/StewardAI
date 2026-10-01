-- Prices stream fixes (audit A1, A15, A16, A17, B21-B24, B26, D3.1):
--
--   price_bar            source CHECK admits every writer (stooq, yahoo, eodhd, alpha_vantage,
--                        twelve_data); Stooq currency/quote_unit derived at write time and
--                        backfilled for existing rows.
--   corporate_action     GBp/ZAc/ILa dividend currencies; stock_dividend needs a ratio (new rows);
--                        explicit owner decision columns (retraction path); (symbol, ex_date) index.
--   exchange_session     index named for what it indexes; `reason` for ad hoc closures.
--   price_return_series  calc_version + inputs_digest: a changed method adds a row instead of
--                        silently overwriting the earlier answer.
--   security_listing / provider_identifier
--                        natural-key unique indexes so the listing writer cannot duplicate rows
--                        through NULL valid_from, and evidence on the listing.
--   refund_provider_credits
--                        gives back credits reserved for a request that never reached the provider.

-- --------------------------------------------------------------------------------------
-- A1: every price writer is admitted by the CHECK
-- --------------------------------------------------------------------------------------
alter table public.price_bar drop constraint if exists price_bar_source_check;
alter table public.price_bar
  add constraint price_bar_source_check
  check (source in ('stooq', 'yahoo', 'eodhd', 'alpha_vantage', 'twelve_data'));

comment on column public.price_bar.source is
  'stooq (source of record), yahoo (unofficial chart API), eodhd and alpha_vantage (gap '
  'fallbacks under free-tier budgets), twelve_data (reserved).';

-- --------------------------------------------------------------------------------------
-- A16 / D3.1: quote currency and unit known at write time
-- --------------------------------------------------------------------------------------
update public.price_bar b
   set currency = m.cur
  from (values ('us', 'USD'), ('uk', 'GBX'), ('de', 'EUR'), ('jp', 'JPY'), ('pl', 'PLN'),
               ('nl', 'EUR'), ('dk', 'DKK'), ('cz', 'CZK')) as m(sfx, cur)
 where b.source = 'stooq' and b.currency is null
   and lower(substring(b.symbol from '\.([A-Za-z]+)$')) = m.sfx;

update public.price_bar
   set quote_unit = case
         when currency in ('GBX', 'GBp') then 'GBX'
         when currency in ('ZAc', 'ZAC') then 'ZAC'
         when currency in ('ILa', 'ILA') then 'ILA'
         else upper(currency) end
 where quote_unit is null and currency is not null;

-- --------------------------------------------------------------------------------------
-- B23 / A15 / B26: corporate actions
-- --------------------------------------------------------------------------------------
alter table public.corporate_action drop constraint if exists corporate_action_currency_check;
alter table public.corporate_action
  add constraint corporate_action_currency_check
  check (currency is null or currency ~ '^[A-Z]{3}$' or currency in ('GBp', 'ZAc', 'ILa'));

-- NOT VALID: existing cash-only stock_dividend rows stay readable (and unusable for returns);
-- new rows must carry a ratio.
alter table public.corporate_action
  add constraint corporate_action_stock_dividend_ratio
  check (action_type <> 'stock_dividend' or ratio is not null) not valid;

alter table public.corporate_action
  add column decided_at timestamptz,
  add column decided_by uuid,
  add column decision_reason text;

comment on column public.corporate_action.decided_at is
  'Set when the owner decided the status explicitly (confirm, reject or retract); a vendor or '
  'collector re-insert never overrides an owner decision.';

create index corporate_action_symbol_ex_date
  on public.corporate_action (symbol, ex_date);

-- --------------------------------------------------------------------------------------
-- B22: exchange_session
-- --------------------------------------------------------------------------------------
-- The earlier name suggested a foreign key to source_evidence; the column is the free-text
-- source label.
alter index if exists public.exchange_session_source_evidence rename to exchange_session_source;

alter table public.exchange_session add column reason text;
comment on column public.exchange_session.reason is
  'Why the day differs from the regular calendar (national holiday observed, ad hoc closure, '
  'early close).';

-- --------------------------------------------------------------------------------------
-- price_return_series versioning
-- --------------------------------------------------------------------------------------
alter table public.price_return_series
  add column calc_version integer not null default 1,
  add column inputs_digest text;

do $$
declare c text;
begin
  select conname into c from pg_constraint
   where conrelid = 'public.price_return_series'::regclass and contype = 'u';
  if c is not null then
    execute format('alter table public.price_return_series drop constraint %I', c);
  end if;
end $$;

create unique index price_return_series_version_key
  on public.price_return_series
  (symbol, start_date, end_date, adjustment_basis, source, calc_version);

comment on column public.price_return_series.calc_version is
  'Version of the return method (split/dividend handling, session calendar). A new method '
  'version stores a new row; the reader takes the newest known_at.';

-- --------------------------------------------------------------------------------------
-- A17: listing writer support
-- --------------------------------------------------------------------------------------
alter table public.security_listing
  add column source text,
  add column evidence jsonb not null default '{}';

-- NULL valid_from made the original unique (security_id, mic, ticker, valid_from) a no-op.
create unique index security_listing_natural_key
  on public.security_listing (security_id, mic, ticker, coalesce(valid_from, date '0001-01-01'));
create unique index provider_identifier_natural_key
  on public.provider_identifier
  (provider, identifier_type, identifier, coalesce(valid_from, date '0001-01-01'));
create index provider_identifier_listing on public.provider_identifier (listing_id)
  where listing_id is not null;

-- --------------------------------------------------------------------------------------
-- B24: give credits back when the request never reached the provider
-- --------------------------------------------------------------------------------------
create or replace function public.refund_provider_credits(
  p_provider text, p_endpoint text, p_cost numeric, p_now timestamptz
) returns void language plpgsql security invoker set search_path = '' as $$
begin
  if p_cost <= 0 then raise exception 'invalid provider credit refund'; end if;
  perform pg_advisory_xact_lock(hashtext(p_provider), hashtext(p_endpoint));
  update public.provider_quota_usage
     set credits = greatest(credits - p_cost, 0), requests = greatest(requests - 1, 0)
   where provider = p_provider and endpoint = p_endpoint
     and ((window_kind = 'minute' and window_start = date_trunc('minute', p_now))
       or (window_kind = 'day' and window_start = date_trunc('day', p_now))
       or (window_kind = 'month' and window_start = date_trunc('month', p_now)));
end $$;
