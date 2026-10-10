# ruff: noqa: E501 - SQL stays on one line per clause
"""Database loaders for discovery. Each query is bounded by the requested tickers and by the
point-in-time cutoff (`known_at`), so a screen run "as of" a date sees only what was known."""

from __future__ import annotations

import datetime as dt
import json
from collections import defaultdict
from decimal import Decimal
from typing import Any

import asyncpg

from steward.discovery import build
from steward.discovery.build import FundInputs, StockInputs
from steward.discovery.engine import Record
from steward.discovery.universe import UniverseMember, UniverseSnapshot, inclusion_reason
from steward.peer_metrics import financial_peer_metrics
from steward.reference.price_series import SOURCE_ORDER, yahoo_from_stooq

ASSET_CONCEPTS = ("assets",)
SHARE_CONCEPTS = (
    "weightedaveragenumberofdilutedsharesoutstanding",
    "weightedaveragenumberofsharesoutstandingbasicanddiluted",
    "adjustedweightedaverageshares",
    "weightedaverageshares",
    "weightedaveragenumberofsharesoutstandingbasic",
)
HISTORY_YEARS = 6


def _member(r: asyncpg.Record) -> UniverseMember:
    return UniverseMember(
        ticker=r["ticker"],
        name=r["name"],
        isin=r["isin"],
        asset_type=r["asset_type"],
        issuer_id=r["issuer_id"],
        security_id=r["security_id"],
        listing_id=r["listing_id"],
        mic=r["mic"],
        exchange=r["exchange"],
        country=r["country"],
        currency=r["currency"],
        status=r["status"] or "active",
        active_from=r["active_from"],
        active_to=r["active_to"],
        identity_status=r["identity_status"],
        has_statements=bool(r["has_statements"]),
        has_prices=bool(r["has_prices"]),
        history_from=r["history_from"],
        price_symbol=r["price_symbol"],
    )


async def load_universe(pool: asyncpg.Pool) -> list[UniverseMember]:
    rows = await pool.fetch("select * from public.investment_universe order by ticker")
    return [_member(r) for r in rows]


async def watchlist_tickers(pool: asyncpg.Pool, owner_id: Any) -> set[str]:
    rows = await pool.fetch(
        "select distinct ticker from public.watchlist_item where owner_id=$1", owner_id
    )
    return {str(r["ticker"]) for r in rows}


async def holding_tickers(pool: asyncpg.Pool) -> set[str]:
    rows = await pool.fetch(
        "select distinct ticker from public.position_snapshot"
        " where snapshot_date=(select max(snapshot_date) from public.position_snapshot)"
    )
    return {str(r["ticker"]) for r in rows}


async def load_snapshot(pool: asyncpg.Pool, snapshot_id: int) -> UniverseSnapshot | None:
    head = await pool.fetchrow(
        "select as_of, known_at, source from public.universe_snapshot where id=$1", snapshot_id
    )
    if head is None:
        return None
    rows = await pool.fetch(
        "select * from public.universe_snapshot_member where snapshot_id=$1 order by ticker",
        snapshot_id,
    )
    members = tuple(
        UniverseMember(
            ticker=r["ticker"],
            name=r["name"],
            isin=r["isin"],
            asset_type=r["asset_type"],
            issuer_id=r["issuer_id"],
            security_id=r["security_id"],
            listing_id=r["listing_id"],
            mic=r["mic"],
            exchange=r["exchange"],
            country=r["country"],
            currency=r["currency"],
            status=r["status"],
            active_from=r["active_from"],
            active_to=r["active_to"],
            has_statements=True,
            has_prices=True,
        )
        for r in rows
    )
    return UniverseSnapshot(head["as_of"], head["known_at"], head["source"], members)


async def snapshot_id_for(pool: asyncpg.Pool, as_of: dt.date) -> int | None:
    value = await pool.fetchval(
        "select id from public.universe_snapshot where as_of <= $1 order by as_of desc limit 1",
        as_of,
    )
    return int(value) if value is not None else None


async def write_snapshot(
    pool: asyncpg.Pool, snapshot: UniverseSnapshot, *, note: str | None = None
) -> int:
    """Store a frozen universe once per (as_of, source). Returns the existing id when the same
    date was already frozen: a snapshot is never rewritten."""
    async with pool.acquire() as conn, conn.transaction():
        existing = await conn.fetchval(
            "select id from public.universe_snapshot where as_of=$1 and source=$2",
            snapshot.as_of,
            snapshot.source,
        )
        if existing is not None:
            return int(existing)
        sid = await conn.fetchval(
            "insert into public.universe_snapshot (as_of, known_at, source, member_count, active_count, note)"
            " values ($1,$2,$3,$4,$5,$6) returning id",
            snapshot.as_of,
            snapshot.known_at,
            snapshot.source,
            len(snapshot.members),
            sum(1 for m in snapshot.members if m.status == "active"),
            note,
        )
        await conn.executemany(
            "insert into public.universe_snapshot_member (snapshot_id, ticker, security_id, listing_id, issuer_id, isin,"
            " name, asset_type, mic, exchange, country, currency, status, active_from, active_to, inclusion_reason)"
            " values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16)",
            [
                (
                    sid,
                    m.ticker,
                    m.security_id,
                    m.listing_id,
                    m.issuer_id,
                    m.isin,
                    m.name,
                    m.asset_type,
                    m.mic,
                    m.exchange,
                    m.country,
                    m.currency,
                    m.status,
                    m.active_from,
                    m.active_to,
                    inclusion_reason(m),
                )
                for m in snapshot.members
            ],
        )
        return int(sid)


async def provider_states(pool: asyncpg.Pool) -> dict[tuple[str, str], str]:
    rows = await pool.fetch("select provider, endpoint, state from public.provider_access")
    return {(r["provider"], r["endpoint"]): r["state"] for r in rows}


def _dimensional(row: dict[str, Any]) -> bool:
    dims = row.get("dimensions")
    if isinstance(dims, str):
        try:
            dims = json.loads(dims)
        except ValueError:
            return False
    return isinstance(dims, dict) and bool(dims.get("members") or dims.get("typed"))


async def _facts(
    pool: asyncpg.Pool, tickers: list[str], cutoff: dt.datetime | None
) -> list[dict[str, Any]]:
    rows = await pool.fetch(
        """select ticker,concept,period_start,period_end,basis,value,unit,dimensions,known_at,validation,published
           from public.financial_statement_fact where ticker=any($1::text[]) and value is not null
             and ($2::timestamptz is null or known_at<=$2) order by ticker,period_end,known_at,concept""",
        tickers,
        cutoff,
    )
    return [dict(r) for r in rows]


def _annual_series(
    facts: list[dict[str, Any]], concepts: tuple[str, ...]
) -> dict[str, list[tuple[dt.date, Decimal]]]:
    """Per ticker: one period-end series from the first concept (in priority order) the ticker
    has; the latest filing wins for a repeated period; shares only for annual durations."""
    by_ticker: dict[str, dict[str, dict[dt.date, tuple[dt.datetime, Decimal]]]] = defaultdict(
        lambda: defaultdict(dict)
    )
    for r in facts:
        local = str(r["concept"]).split(":")[-1].lower()
        if local not in concepts or _dimensional(r) or not isinstance(r["value"], Decimal):
            continue
        start, end = r["period_start"], r["period_end"]
        if start is not None and not 330 <= (end - start).days <= 380:
            continue
        known = r["known_at"] or dt.datetime.min.replace(tzinfo=dt.UTC)
        slot = by_ticker[r["ticker"]][local]
        if end not in slot or known >= slot[end][0]:
            slot[end] = (known, r["value"])
    out: dict[str, list[tuple[dt.date, Decimal]]] = {}
    for ticker, per_concept in by_ticker.items():
        for concept in concepts:
            if concept in per_concept:
                out[ticker] = sorted((d, v) for d, (_, v) in per_concept[concept].items())
                break
    return out


async def closes_between(
    pool: asyncpg.Pool, members: list[UniverseMember], start: dt.date, end: dt.date
) -> dict[str, list[tuple[dt.date, Decimal]]]:
    """Stored adjusted closes per ticker between two dates (the same source rules as screens)."""
    return await _closes(pool, members, end, start=start)


async def _closes(
    pool: asyncpg.Pool,
    members: list[UniverseMember],
    as_of: dt.date,
    *,
    start: dt.date | None = None,
) -> dict[str, list[tuple[dt.date, Decimal]]]:
    symbols: dict[str, list[str]] = {}
    for m in members:
        if m.price_symbol:
            alt = yahoo_from_stooq(m.price_symbol)
            symbols[m.ticker] = [m.price_symbol] + ([alt] if alt and alt != m.price_symbol else [])
    if not symbols:
        return {}
    wanted = sorted({s for v in symbols.values() for s in v})
    rows = await pool.fetch(
        """select symbol, source, date, close from public.price_bar
           where symbol=any($1::text[]) and bar_type='adjusted' and access_state='available'
             and date between $2 and $3 order by symbol, source, date""",
        wanted,
        start or as_of - dt.timedelta(days=365 * HISTORY_YEARS),
        as_of,
    )
    series: dict[tuple[str, str], list[tuple[dt.date, Decimal]]] = defaultdict(list)
    for r in rows:
        series[(r["symbol"], r["source"])].append((r["date"], r["close"]))
    order = {s: i for i, s in enumerate(SOURCE_ORDER)}
    out: dict[str, list[tuple[dt.date, Decimal]]] = {}
    for ticker, syms in symbols.items():
        cands = [(sym, src) for sym in syms for src in SOURCE_ORDER if series.get((sym, src))]
        if cands:
            # Newest last bar wins; the source order breaks ties (Stooq is the source of record).
            best = max(cands, key=lambda c: (series[c][-1][0], -order[c[1]]))
            out[ticker] = series[best]
    return out


async def load_stock_inputs(
    pool: asyncpg.Pool,
    members: list[UniverseMember],
    as_of: dt.date,
    *,
    cutoff: dt.datetime | None,
    reconciled_only: bool,
    access: dict[tuple[str, str], str],
) -> dict[str, StockInputs]:
    tickers = sorted({m.ticker for m in members})
    out: dict[str, StockInputs] = {t: StockInputs() for t in tickers}
    if not tickers:
        return out
    facts = await _facts(pool, tickers, cutoff)
    metrics = financial_peer_metrics(facts, reconciled_only=reconciled_only)
    for t, data in metrics.items():
        if t in out:
            out[t].periods = list(data["periods"])  # type: ignore[call-overload]
    for t, series in _annual_series(facts, ASSET_CONCEPTS).items():
        if t in out:
            out[t].assets = series
    for t, series in _annual_series(facts, SHARE_CONCEPTS).items():
        if t in out:
            out[t].shares = series
            out[t].restated_shares = series
    if cutoff is not None:
        # Filings after the cutoff restate earlier years for a split; only the share counts are
        # read past it, and only to put the filed count on the basis of today's adjusted closes.
        later = await pool.fetch(
            """select ticker,concept,period_start,period_end,value,dimensions,known_at
               from public.financial_statement_fact where ticker=any($1::text[]) and value is not null
                 and lower(regexp_replace(concept, '^.*:', '')) = any($2::text[])""",
            tickers,
            list(SHARE_CONCEPTS),
        )
        for t, series in _annual_series([dict(r) for r in later], SHARE_CONCEPTS).items():
            if t in out:
                out[t].restated_shares = series
    for t, closes in (await _closes(pool, members, as_of)).items():
        out[t].closes = closes

    by_listing = {m.listing_id: m.ticker for m in members if m.listing_id}
    if by_listing:
        # Splits scale the filed share counts onto the basis of the split-adjusted closes.
        for r in await pool.fetch(
            """select listing_id, ex_date, ratio from public.corporate_action
               where listing_id=any($1::bigint[]) and action_type='split' and status='confirmed'
                 and ratio is not null and ratio > 0 order by ex_date""",
            list(by_listing),
        ):
            out[by_listing[r["listing_id"]]].splits.append((r["ex_date"], r["ratio"]))
        rows = await pool.fetch(
            """select listing_id, currency, ex_date, cash_amount from public.corporate_action
               where listing_id=any($1::bigint[]) and action_type='cash_dividend' and status='confirmed'
                 and cash_amount is not null and ex_date > $2 and ex_date <= $3
                 and ($4::timestamptz is null or known_at<=$4)""",
            list(by_listing),
            as_of - dt.timedelta(days=365),
            as_of,
            cutoff,
        )
        seen: dict[str, set[str | None]] = defaultdict(set)
        for r in rows:
            t = by_listing[r["listing_id"]]
            seen[t].add(r["currency"])
            # Declared per share as it stood on the ex-date; the close is split-adjusted, so the
            # amount is divided by every split after the ex-date.
            factor = Decimal(1)
            for ex, ratio in out[t].splits:
                if ex > r["ex_date"]:
                    factor *= ratio
            out[t].dividends_ttm = (out[t].dividends_ttm or Decimal(0)) + r["cash_amount"] / factor
            out[t].dividend_currency = r["currency"]
        for t, ccys in seen.items():
            if len(ccys) > 1:
                out[t].dividends_ttm, out[t].dividend_currency = None, None
    isins = sorted({m.isin for m in members if m.isin})
    if isins:
        rows = await pool.fetch(
            "select isin, avg_daily_turnover, turnover_currency from public.listing_liquidity"
            " where isin=any($1::text[]) and avg_daily_turnover is not null",
            isins,
        )
        by_isin = {r["isin"]: (r["avg_daily_turnover"], r["turnover_currency"]) for r in rows}
        first = await pool.fetch(
            "select isin, min(first_trade_at)::date as first_trade from public.listing"
            " where isin=any($1::text[]) and first_trade_at is not null group by isin",
            isins,
        )
        first_by = {r["isin"]: r["first_trade"] for r in first}
        for m in members:
            if m.isin in by_isin and by_isin[m.isin][1]:
                out[m.ticker].turnover = (by_isin[m.isin][0], by_isin[m.isin][1])
            if m.isin in first_by:
                out[m.ticker].first_trade = first_by[m.isin]
    prof = await pool.fetch(
        """select distinct on (ticker, metric) ticker, metric, text_value from public.company_research_observation
           where provider='fmp' and dataset='profile' and metric in ('sector','industry') and ticker=any($1::text[])
             and text_value is not null and ($2::timestamptz is null or known_at<=$2)
           order by ticker, metric, known_at desc""",
        tickers,
        cutoff,
    )
    for r in prof:
        setattr(out[r["ticker"]], r["metric"], r["text_value"])
    # Insider cluster: distinct insiders with counted open-market buys in 90 days.
    ins = await pool.fetch(
        """select f.ticker, count(distinct t.reporting_owner_cik) as buyers
           from public.insider_filing f join public.insider_transaction t using (accession)
           where f.ticker=any($1::text[]) and t.economic_class='open_market_buy'
             and not exists (select 1 from public.insider_filing s
                             where s.accession=f.superseded_by_accession and s.filed <= $3)
             and not t.derivative and coalesce(t.rule_10b5_1_plan,false)=false and t.shares > 0
             and coalesce(t.transaction_date, f.filed) between $2 and $3 and f.filed <= $3
           group by f.ticker""",
        tickers,
        as_of - dt.timedelta(days=90),
        as_of,
    )
    covered = {r["ticker"]: int(r["buyers"]) for r in ins}
    has_filings = {
        r["ticker"]
        for r in await pool.fetch(
            "select distinct ticker from public.insider_filing where ticker=any($1::text[]) and filed <= $2",
            tickers,
            as_of,
        )
    }
    for t in has_filings:
        out[t].insider_buyers_90d = covered.get(t, 0)
    if access.get(("fmp", "analyst_estimates")) == "available":
        est = await pool.fetch(
            """select distinct on (ticker) ticker, value, currency from public.forward_observation
               where kind='published_analyst_consensus' and metric='earnings_per_share' and ticker=any($1::text[])
                 and value is not null and period_end > $2 and ($3::timestamptz is null or known_at<=$3)
                 and provider is not null and provider<>'' and fiscal_period is not null and fiscal_period<>''
                 and basis is not null and basis<>'' and currency is not null and currency<>''
                 and analyst_count is not null
               order by ticker, period_end, known_at desc""",
            tickers,
            as_of,
            cutoff,
        )
        for r in est:
            out[r["ticker"]].forward_eps = r["value"]
            out[r["ticker"]].forward_currency = r["currency"]
    return out


async def stock_records(
    pool: asyncpg.Pool,
    members: list[UniverseMember],
    as_of: dt.date,
    *,
    cutoff: dt.datetime | None,
    reconciled_only: bool,
    access: dict[tuple[str, str], str],
) -> list[Record]:
    inputs = await load_stock_inputs(
        pool, members, as_of, cutoff=cutoff, reconciled_only=reconciled_only, access=access
    )
    return [build.stock_record(m, inputs[m.ticker], as_of) for m in members]


async def fund_inputs(pool: asyncpg.Pool, as_of: dt.date) -> list[FundInputs]:
    """Latest snapshot and factsheet per fund (point in time: only rows fetched by `as_of`)."""
    heads = await pool.fetch(
        """select distinct on (fund_isin) id, fund_isin, as_of, fund_name from public.etf_snapshot
           where as_of <= $1 and fetched_at < (($1::date + 1)::timestamp at time zone 'UTC')
           order by fund_isin, as_of desc, fetched_at desc, id desc""",
        as_of,
    )
    sheets = await pool.fetch(
        """select distinct on (fund_isin) * from public.fund_factsheet
           where as_of <= $1 and fetched_at < (($1::date + 1)::timestamp at time zone 'UTC')
           order by fund_isin, as_of desc, fetched_at desc, id desc""",
        as_of,
    )
    sheet_by = {r["fund_isin"]: dict(r) for r in sheets}
    ids = [r["id"] for r in heads]
    holdings: dict[int, list[dict[str, Any]]] = defaultdict(list)
    if ids:
        for r in await pool.fetch(
            "select snapshot_id,row_index,isin,name,ticker,asset_class,weight,sector,country"
            " from public.etf_holding where snapshot_id=any($1::bigint[])",
            ids,
        ):
            holdings[r["snapshot_id"]].append(dict(r))
    out: list[FundInputs] = []
    seen = set()
    for h in heads:
        sheet = sheet_by.get(h["fund_isin"], {})
        out.append(
            FundInputs(
                h["fund_isin"],
                h["fund_name"],
                h["as_of"],
                sheet,
                holdings.get(h["id"], []),
                sheet.get("lookthrough_basis"),
            )
        )
        seen.add(h["fund_isin"])
    for isin, sheet in sheet_by.items():  # factsheet without holdings: fees known, holdings unknown
        if isin not in seen:
            out.append(
                FundInputs(
                    isin, sheet.get("fund_name"), None, sheet, [], sheet.get("lookthrough_basis")
                )
            )
    return sorted(out, key=lambda f: f.isin)
