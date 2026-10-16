-- Insight data readiness: what the insight features (plan I0-I14) have in the database right now.
-- Read-only: every statement is a SELECT. Each block is independent, so a missing table only fails
-- its own block. Run against the live database with the URL taken from your environment:
--
--   psql "$DATABASE_URL" -f docs/data/insight_data_readiness.sql
--
-- Thresholds quoted in the comments are the code's own (see docs/data/insight_data_plan.md).

\pset pager off
\echo '== 1. Holdings in the latest snapshot'
select max(snapshot_date) as latest_snapshot, count(*) as holdings,
       (select count(distinct snapshot_date) from public.position_snapshot) as snapshot_days,
       (select min(snapshot_date) from public.position_snapshot) as first_snapshot
from public.position_snapshot
where snapshot_date = (select max(snapshot_date) from public.position_snapshot);

\echo '== 2. Per holding: what each feature can read (one row per holding)'
with held as (
  select p.ticker, i.name, i.price_symbol, i.cik, i.home_country, lnk.listing_id
  from public.position_snapshot p left join public.instrument i on i.t212_ticker = p.ticker left join public.instrument_security_link lnk on lnk.ticker = p.ticker
  where p.snapshot_date = (select max(snapshot_date) from public.position_snapshot)
)
select h.ticker,
       left(coalesce(h.name, ''), 24) as name,
       h.cik is not null as sec_registrant,
       h.home_country,
       -- prices: risk needs 60 (volatility, beta), 250 (VaR) daily closes; valuation history 730 days
       (select count(*) from public.price_bar b where (b.symbol = h.price_symbol or b.listing_id = h.listing_id) and b.date > current_date - 400) as closes_400d,
       -- dividends: ready = confirmed events over >= 3 calendar years; estimate needs >= 4 payments
       (select count(*) from public.corporate_action c where (c.listing_id = h.listing_id or lower(c.symbol) in (lower(h.price_symbol), lower(split_part(h.ticker, '_', 1)))) and c.action_type = 'cash_dividend' and c.status = 'confirmed') as div_confirmed,
       (select count(*) from public.corporate_action c where (c.listing_id = h.listing_id or lower(c.symbol) in (lower(h.price_symbol), lower(split_part(h.ticker, '_', 1)))) and c.action_type = 'cash_dividend' and c.status = 'suggested') as div_suggested,
       (select count(distinct extract(year from c.ex_date)) from public.corporate_action c where (c.listing_id = h.listing_id or lower(c.symbol) in (lower(h.price_symbol), lower(split_part(h.ticker, '_', 1)))) and c.action_type = 'cash_dividend' and c.status <> 'rejected') as div_years,
       (select count(*) from public.transaction t where t.ticker = h.ticker and t.type = 'dividend') as t212_dividends,
       -- statements: checks, payout, health, ROE; FY facts without dimensions
       (select count(distinct f.line_item) from public.financial_statement_fact f where f.ticker = h.ticker
          and f.line_item in ('dividends_paid','dividends_per_share','weighted_shares_diluted','interest_expense','cash_from_operations','net_income','total_liabilities')
          and coalesce(f.dimensions->'members','[]'::jsonb) = '[]'::jsonb) as filed_inputs_of_7,
       (select count(distinct extract(year from f.period_end)) from public.financial_statement_fact f where f.ticker = h.ticker and f.line_item = 'net_income') as ni_years,
       -- estimates (future axis): >= 2 future-period consensus rows
       (select count(*) from public.forward_observation o where o.ticker = h.ticker and o.kind = 'published_analyst_consensus' and o.period_end > current_date) as estimate_rows,
       -- price target: latest snapshot state; chart needs 8 snapshots, track record 12 months
       (select s.access_state from public.consensus_snapshot s where s.ticker = h.ticker order by s.as_of desc limit 1) as target_state,
       (select count(*) from public.consensus_snapshot s where s.ticker = h.ticker and s.access_state = 'complete') as target_snaps,
       -- peers: a median needs 5 (confirmed peer_set rows, a saved group, or the sector universe)
       (select count(distinct ps.peer_ticker) from public.peer_set ps where ps.ticker = h.ticker and ps.status = 'confirmed') as peers_confirmed,
       (select count(distinct ps.peer_ticker) from public.peer_set ps where ps.ticker = h.ticker and ps.status = 'suggested') as peers_suggested,
       -- fair value history: usable at 8 dated points
       (select count(distinct v.as_of) from public.fair_value_point v where v.ticker = h.ticker) as fv_days,
       (select count(*) from public.dividend_forecast_snapshot d where d.ticker = h.ticker) as div_forecast_snaps,
       (select count(*) from public.company_check_snapshot k where k.ticker = h.ticker) as check_snaps,
       -- ownership / governance (US): Form 4, 13F, proxy pay
       (select count(*) from public.insider_filing_ticker x where x.ticker = h.ticker) as form4_filings,
       (select count(*) from public.institutional_holding ih where ih.ticker = h.ticker) as f13_rows,
       (select count(*) from public.financial_statement_fact f where f.ticker = h.ticker and f.form like 'DEF 14A%') as proxy_pay_facts,
       -- earnings summary: stored release document with extracted figures
       (select count(distinct e.sha256) from public.fact f join public.evidence e on e.sha256 = f.evidence_sha256 where f.ticker = h.ticker) as release_docs,
       -- geography (diversification): geographic segment revenue
       (select count(*) from public.company_segment_map m where m.ticker = h.ticker and m.breakdown = 'geographic') as geo_map_rows
from held h
order by h.ticker;

\echo '== 3. Endpoint access verdicts (second dividend source, price targets, estimates)'
select provider, endpoint, state, tested_at::date, status_code, left(coalesce(detail, ''), 70) as detail
from public.provider_access order by provider, endpoint;

\echo '== 4. Clocks: first and latest stored snapshot of each accumulating series'
select 'dividend_forecast_snapshot' as series, min(as_of) as first, max(as_of) as latest, count(distinct as_of) as days from public.dividend_forecast_snapshot
union all select 'consensus_snapshot', min(as_of), max(as_of), count(distinct as_of) from public.consensus_snapshot
union all select 'fair_value_point', min(as_of), max(as_of), count(distinct as_of) from public.fair_value_point
union all select 'position_snapshot', min(snapshot_date), max(snapshot_date), count(distinct snapshot_date) from public.position_snapshot;

\echo '== 5. Benchmark and FX: risk score needs the benchmark closes, money in other currencies needs FX'
select symbol, count(*) as closes, min(date), max(date) from public.price_bar where symbol like '^%' group by symbol order by symbol;
select source, currency, home_currency, count(*) as days, max(rate_date) as latest from public.fx_rate group by 1, 2, 3 order by 1, 2, 3;

\echo '== 6. Last run of every job that fills insight data'
select job_type, max(finished_at) filter (where status = 'succeeded') as last_success,
       max(finished_at) filter (where status = 'failed') as last_failure,
       count(*) filter (where created_at > now() - interval '7 days') as runs_7d
from public.job
where job_type in ('portfolio_sync', 'reference_data', 'source_audit', 'price_history',
                   'sec_xbrl', 'valuation_refresh', 'sec_fsds_backfill', 'alpha_vantage_statements',
                   'fmp_company', 'ai_approvals', 'twelve_data_corporate_actions', 'yahoo_dividends',
                   'dividend_forecast', 'insight_access_probe', 'consensus_snapshot',
                   'fair_value_snapshot', 'checks_refresh', 'ownership_13f', 'insider_trades',
                   'earnings_check', 'earnings_summary', 'news_ingest', 'etf_look_through')
group by job_type order by job_type;

\echo '== 7. All job types seen in the last 30 days (names may differ from the list above)'
select job_type, count(*) as runs, max(finished_at) as last_finished,
       sum((status = 'failed')::int) as failed
from public.job where created_at > now() - interval '30 days'
group by job_type order by job_type;
