"""More read-only chat tools over data the database already holds: FX rates, the latest quote,
listing liquidity, raw statement lines, share counts, segment revenue, corporate actions,
peers, ETF breakdowns, fund NAV and the data-coverage review queue.

Same rules as `research_tools`: plain parameterised reads (nothing writes, the model supplies
no SQL), bounded rows, a registry source for what is returned so the answer can cite it, and
every figure in the shared envelope so a missing value, a stale one and a zero stay apart.
"""

import datetime as dt
from collections import defaultdict
from typing import Any

import asyncpg

from steward.chat import envelope as env
from steward.research.sources import Registry

QUOTE_STALE_DAYS = 4
FX_STALE_DAYS = 7
NAV_STALE_DAYS = 10
LIQUIDITY_BARS = 30
STATEMENTS = ("income", "cash_flow", "balance_sheet", "shares", "segment", "note")
SEGMENT_KINDS = ("geographic", "segment", "product", "total")
ACTION_TYPES = (
    "split",
    "cash_dividend",
    "stock_dividend",
    "symbol_change",
    "merger",
    "spin_off",
    "delisting",
)


def _today() -> dt.date:
    return dt.datetime.now(dt.UTC).date()


def _not_covered(ticker: str) -> dict[str, Any]:
    return {"error": f"{ticker} is not in the covered universe"}


async def _universe(pool: asyncpg.Pool, ticker: str) -> asyncpg.Record | None:
    return await pool.fetchrow(
        "select ticker, isin, price_symbol, currency from public.investment_universe"
        " where ticker = $1",
        ticker,
    )


def _symbol(row: asyncpg.Record) -> str:
    return str(row["price_symbol"] or row["ticker"])


# ------------------------------------------------------------------ FX


async def fx_rates(
    pool: asyncpg.Pool, registry: Registry, currency: str, days: Any, limit: Any
) -> dict[str, Any]:
    """Stored ECB and CNB fixings for one currency, newest first."""
    code = str(currency or "").upper()
    if len(code) != 3 or not code.isalpha():
        return {"error": "currency must be a three-letter code such as USD"}
    span = env.clamp_limit(days, default=30, maximum=365)
    rows_n = env.clamp_limit(limit, default=10, maximum=60)
    since = _today() - dt.timedelta(days=span)
    rows = await pool.fetch(
        "select source, rate_date, currency, home_currency, rate, published_amount"
        " from public.fx_rate where currency = $1 and rate_date >= $2"
        " order by rate_date desc, source limit $3",
        code,
        since,
        rows_n,
    )
    if not rows:
        return {
            "currency": code,
            "status": "missing",
            "reason": f"no stored fixing for {code} in the last {span} days",
        }
    newest = rows[0]["rate_date"]
    status = env.age_status(newest, _today(), FX_STALE_DAYS)
    snippet = "; ".join(
        f"{r['source']} {r['rate_date']}: 1 {code} = {r['rate']:.6g} {r['home_currency']}"
        for r in rows[:6]
    )
    sid = registry.add(
        "observation",
        f"fx:{code}:{newest}",
        f"{code} exchange rates to {newest}",
        tier=1,
        snippet=snippet,
        as_of=str(newest),
    )
    out = [
        {
            "source": r["source"],
            "date": r["rate_date"].isoformat(),
            "home_currency": r["home_currency"],
            "rate": env.datum(
                r["rate"],
                unit="home_currency_per_unit",
                currency=r["home_currency"],
                source_id=sid,
                as_of=r["rate_date"],
                reason="units of home currency for one unit of the currency",
            ),
        }
        for r in rows
    ]
    # ECB fixings are against EUR and CNB's against CZK: never compare across sources, and
    # take "the latest" per source rather than whichever sorts first.
    newest_per_source: dict[str, asyncpg.Record] = {}
    for r in rows:
        newest_per_source.setdefault(r["source"], r)
    return {
        "currency": code,
        "source_id": sid,
        "latest": env.datum(
            rows[0]["rate"],
            status=status,
            as_of=newest,
            unit="home_currency_per_unit",
            currency=rows[0]["home_currency"],
            source_id=sid,
            reason="latest stored fixing is old" if status == "stale" else None,
        ),
        "latest_by_source": {
            source: {
                "home_currency": r["home_currency"],
                "date": r["rate_date"].isoformat(),
                "rate": env.datum(
                    r["rate"],
                    unit="home_currency_per_unit",
                    currency=r["home_currency"],
                    source_id=sid,
                    as_of=r["rate_date"],
                ),
            }
            for source, r in newest_per_source.items()
        },
        "rates": out,
        "page": env.page(out, rows_n),
    }


# ------------------------------------------------------------------ quotes and liquidity


async def latest_quote(pool: asyncpg.Pool, registry: Registry, ticker: str) -> dict[str, Any]:
    """The most recent stored quote (intraday or close) and the previous close."""
    member = await _universe(pool, ticker)
    if member is None:
        return _not_covered(ticker)
    symbol = _symbol(member)
    rows = await pool.fetch(
        "select source, kind, price, price_time, session_date, previous_close, currency"
        " from public.market_quote where symbol = $1 order by price_time desc limit 3",
        symbol,
    )
    if not rows:
        return {"ticker": ticker, "status": "missing", "reason": "no stored quote for this ticker"}
    q = rows[0]
    status = env.age_status(q["session_date"], _today(), QUOTE_STALE_DAYS)
    sid = registry.add(
        "price",
        f"quote:{symbol}:{q['source']}:{q['price_time']:%Y%m%d%H%M}",
        f"{ticker} {q['kind']} quote from {q['source']}",
        app_path=f"/holdings/{ticker}",
        tier=2,
        snippet=(
            f"{ticker} {q['kind']} {q['price']:.6g} {q['currency'] or '(currency not stored)'} "
            f"at {q['price_time']:%Y-%m-%d %H:%M} UTC"
        ),
        as_of=str(q["session_date"]),
    )
    prev = q["previous_close"]
    change = float(q["price"] / prev - 1) if prev else None
    return {
        "ticker": ticker,
        "source_id": sid,
        "quote": env.datum(
            q["price"],
            status=status,
            as_of=q["price_time"],
            unit="price",
            currency=q["currency"],
            source_id=sid,
            reason="latest stored quote is more than a few days old" if status == "stale" else None,
        ),
        "kind": q["kind"],
        "provider": q["source"],
        "session_date": q["session_date"].isoformat(),
        "previous_close": env.datum(prev, unit="price", currency=q["currency"], source_id=sid)
        if prev
        else env.absent("missing", "no previous close stored"),
        "change_since_previous_close": env.datum(change, unit="fraction", source_id=sid)
        if change is not None
        else env.absent("missing", "no previous close stored"),
        "other_providers": [
            {"provider": r["source"], "kind": r["kind"], "price": env.plain(r["price"])}
            for r in rows[1:]
        ],
    }


async def listing_liquidity(pool: asyncpg.Pool, registry: Registry, ticker: str) -> dict[str, Any]:
    """Regulatory liquidity data for the listing's ISIN plus the average traded volume over the
    last stored daily bars. A missing figure says so."""
    member = await _universe(pool, ticker)
    if member is None:
        return _not_covered(ticker)
    isin = member["isin"]
    liq = (
        await pool.fetchrow(
            "select liquid, avg_daily_turnover, turnover_currency, avg_daily_trades,"
            " period_from, period_to from public.listing_liquidity where isin = $1",
            isin,
        )
        if isin
        else None
    )
    bars = await pool.fetch(
        "select date, volume from public.price_bar where symbol = $1 and volume is not null"
        " order by date desc limit $2",
        _symbol(member),
        LIQUIDITY_BARS,
    )
    sid = registry.add(
        "observation",
        f"liquidity:{ticker}:{bars[0]['date'] if bars else 'none'}",
        f"{ticker} liquidity",
        app_path=f"/holdings/{ticker}",
        tier=2,
        snippet=(
            f"avg volume {sum(b['volume'] for b in bars) / len(bars):.6g} over {len(bars)} bars"
            if bars
            else None
        ),
        as_of=str(bars[0]["date"]) if bars else None,
    )
    if liq is None:
        regulatory: dict[str, Any] = env.absent(
            "missing",
            "no ISIN for this ticker" if not isin else "no regulatory liquidity record",
            source_id=sid,
        )
    else:
        regulatory = {
            "liquid": liq["liquid"],
            "avg_daily_turnover": env.datum(
                liq["avg_daily_turnover"],
                unit="money",
                currency=liq["turnover_currency"],
                period=f"{liq['period_from']}..{liq['period_to']}",
                source_id=sid,
            )
            if liq["avg_daily_turnover"] is not None
            else env.absent("missing", "no turnover reported", source_id=sid),
            "avg_daily_trades": env.plain(liq["avg_daily_trades"]),
        }
    volume = (
        env.datum(
            sum(b["volume"] for b in bars) / len(bars),
            unit="shares_per_day",
            period=f"last {len(bars)} bars",
            as_of=bars[0]["date"],
            source_id=sid,
        )
        if bars
        else env.absent("missing", "no daily volume stored", unit="shares_per_day")
    )
    return {"ticker": ticker, "source_id": sid, "regulatory": regulatory, "avg_volume": volume}


# ------------------------------------------------------------------ statements


async def statement_lines(
    pool: asyncpg.Pool,
    registry: Registry,
    ticker: str,
    statement: str | None,
    concept: str | None,
    limit: Any,
) -> dict[str, Any]:
    """Raw filed statement lines (as published, latest revision, no dimensions)."""
    if statement is not None and statement not in STATEMENTS:
        return {"error": f"statement must be one of {', '.join(STATEMENTS)}"}
    rows_n = env.clamp_limit(limit, default=20, maximum=60)
    pattern = f"%{concept.strip()}%" if concept and concept.strip() else None
    # Latest revision per line first (distinct on), then the newest periods across all
    # concepts: the limit must cut by date, not by concept name.
    rows = await pool.fetch(
        "select * from (select distinct on (statement, concept, period_start, period_end,"
        " fiscal_period, basis, unit)"
        " statement, concept, label, period_start, period_end, fiscal_period, value, unit,"
        " currency, source_url, form, known_at from public.financial_statement_fact"
        " where ticker = $1 and value is not null"
        " and coalesce(dimensions->'members', '[]'::jsonb) = '[]'::jsonb"
        " and ($2::text is null or statement = $2) and ($3::text is null or concept ilike $3)"
        " order by statement, concept, period_start, period_end desc, fiscal_period, basis, unit,"
        " revision desc) latest"
        " order by period_end desc, statement, concept, fiscal_period limit $4",
        ticker,
        statement,
        pattern,
        rows_n + 1,
    )
    if not rows:
        return {"ticker": ticker, "status": "missing", "reason": "no filed statement lines"}
    shown = sorted(rows[:rows_n], key=lambda r: r["period_end"], reverse=True)
    lines: list[dict[str, Any]] = []
    for r in shown:
        sid = registry.add(
            "observation",
            r["source_url"],
            f"{ticker} {r['label'] or r['concept']} {r['period_end']}"[:200],
            url=r["source_url"],
            tier=1,
            snippet=f"{r['concept']} {r['value']} {r['unit']} for period ending {r['period_end']}",
            as_of=str(r["known_at"].date()),
        )
        lines.append(
            {
                "statement": r["statement"],
                "concept": r["concept"],
                "label": r["label"],
                "form": r["form"],
                "line": env.datum(
                    r["value"],
                    unit=r["unit"],
                    currency=r["currency"],
                    period=(
                        f"{r['fiscal_period'] or ''} {r['period_start'] or ''}..{r['period_end']}"
                    ).strip(),
                    as_of=r["period_end"],
                    source_id=sid,
                ),
            }
        )
    return {
        "ticker": ticker,
        "lines": lines,
        "page": env.page(lines, rows_n, rows_n + 1 if len(rows) > rows_n else len(lines)),
        "note": "Values are as filed, in the unit shown; compare lines only within one unit "
        "and period.",
    }


async def share_counts(pool: asyncpg.Pool, registry: Registry, ticker: str) -> dict[str, Any]:
    """Shares outstanding history from filings (newest first, at most eight)."""
    rows = await pool.fetch(
        "select shares, as_of, method, form, accession from public.shares_outstanding"
        " where ticker = $1 order by as_of desc, fetched_at desc limit 8",
        ticker,
    )
    if not rows:
        return {"ticker": ticker, "status": "missing", "reason": "no share count is stored"}
    latest = rows[0]
    sid = registry.add(
        "observation",
        f"shares:{ticker}:{latest['as_of']}:{latest['method']}",
        f"{ticker} shares outstanding {latest['as_of']}",
        tier=1,
        snippet=f"{latest['shares']:.12g} shares outstanding on {latest['as_of']}",
        as_of=str(latest["as_of"]),
    )
    status = env.age_status(latest["as_of"], _today(), 400)
    history = [
        {
            "as_of": r["as_of"].isoformat(),
            "method": r["method"],
            "shares": env.datum(r["shares"], unit="shares", as_of=r["as_of"], source_id=sid),
        }
        for r in rows
    ]
    return {
        "ticker": ticker,
        "source_id": sid,
        "latest": env.datum(
            latest["shares"], status=status, as_of=latest["as_of"], unit="shares", source_id=sid
        ),
        "history": history,
        "note": "Counts from different methods can differ (share classes); compare like methods.",
    }


async def segment_revenue(
    pool: asyncpg.Pool, registry: Registry, ticker: str, kind: str | None, limit: Any
) -> dict[str, Any]:
    """Revenue by segment, product or geography from the parsed filing, latest period first."""
    if kind is not None and kind not in SEGMENT_KINDS:
        return {"error": f"kind must be one of {', '.join(SEGMENT_KINDS)}"}
    rows_n = env.clamp_limit(limit, default=20, maximum=60)
    # A 10-K restates prior years: keep one row per segment and period, from the newest filing.
    rows = await pool.fetch(
        "select * from (select distinct on (s.kind, s.member, s.period_start, s.period_end)"
        " s.kind, s.axis, s.member, s.label, s.period_start, s.period_end, s.value,"
        " s.unit, s.subtotal, f.form, f.accession, f.instance_url"
        " from public.segment_revenue s join public.xbrl_filing f on f.id = s.filing_id"
        " where f.ticker = $1 and ($2::text is null or s.kind = $2)"
        " order by s.kind, s.member, s.period_start, s.period_end, f.id desc) latest"
        " order by period_end desc, kind, value desc limit $3",
        ticker,
        kind,
        rows_n,
    )
    if not rows:
        return {"ticker": ticker, "status": "missing", "reason": "no segment revenue is stored"}
    items: list[dict[str, Any]] = []
    for r in rows:
        sid = registry.add(
            "observation",
            f"{r['instance_url']}#{r['kind']}:{r['member']}:{r['period_end']}",
            f"{ticker} {r['label']} revenue {r['period_end']}"[:200],
            url=r["instance_url"],
            tier=1,
            snippet=(
                f"{r['label']} ({r['kind']}) {r['value']} {r['unit']} "
                f"for {r['period_start']}..{r['period_end']}"
            ),
            as_of=str(r["period_end"]),
        )
        items.append(
            {
                "kind": r["kind"],
                "label": r["label"],
                "subtotal": r["subtotal"],
                "form": r["form"],
                "revenue": env.datum(
                    r["value"],
                    unit=r["unit"],
                    period=f"{r['period_start']}..{r['period_end']}",
                    as_of=r["period_end"],
                    source_id=sid,
                ),
            }
        )
    return {
        "ticker": ticker,
        "items": items,
        "page": env.page(items, rows_n),
        "note": "Rows marked subtotal include other rows; do not add them together.",
    }


# ------------------------------------------------------------------ corporate actions, peers


async def corporate_actions(
    pool: asyncpg.Pool, registry: Registry, ticker: str, days: Any, limit: Any
) -> dict[str, Any]:
    """Splits, dividends, mergers and similar events by ex-date (confirmed or suggested)."""
    member = await _universe(pool, ticker)
    if member is None:
        return _not_covered(ticker)
    span = env.clamp_limit(days, default=730, maximum=3650)
    rows_n = env.clamp_limit(limit, default=20, maximum=60)
    rows = await pool.fetch(
        "select action_type, ex_date, record_date, payment_date, ratio, cash_amount, currency,"
        " source, source_url, status from public.corporate_action"
        " where symbol = any($1::text[]) and status <> 'rejected' and ex_date >= $2"
        " order by ex_date desc, action_type, id desc limit $3",
        list({_symbol(member), ticker}),
        _today() - dt.timedelta(days=span),
        rows_n,
    )
    if not rows:
        return {
            "ticker": ticker,
            "status": "missing",
            "reason": f"no corporate action stored in the last {span} days",
        }
    items: list[dict[str, Any]] = []
    for r in rows:
        sid = registry.add(
            "observation",
            r["source_url"],
            f"{ticker} {r['action_type']} ex {r['ex_date']}",
            url=r["source_url"],
            tier=1,
            snippet=(
                f"{r['action_type']} ex-date {r['ex_date']} ratio {r['ratio']} "
                f"cash {r['cash_amount']} {r['currency']}"
            ),
            as_of=str(r["ex_date"]),
        )
        items.append(
            {
                "type": r["action_type"],
                "ex_date": r["ex_date"].isoformat(),
                "payment_date": env.plain(r["payment_date"]),
                "status": r["status"],
                "ratio": env.plain(r["ratio"]),
                "cash_amount": env.datum(
                    r["cash_amount"], unit="per_share", currency=r["currency"], source_id=sid
                )
                if r["cash_amount"] is not None
                else env.absent("not_applicable", "not a cash action", source_id=sid),
            }
        )
    return {"ticker": ticker, "actions": items, "page": env.page(items, rows_n)}


async def peers(pool: asyncpg.Pool, registry: Registry, ticker: str) -> dict[str, Any]:
    """Stored peers of a company with how each was chosen; suggested ones are marked."""
    rows = await pool.fetch(
        "select peer_ticker, basis, status, source from public.peer_set"
        " where ticker = $1 and status <> 'rejected'"
        " and (valid_to is null or valid_to >= current_date)"
        " order by (status = 'confirmed') desc, peer_ticker limit 25",
        ticker,
    )
    if not rows:
        return {"ticker": ticker, "status": "missing", "reason": "no peers are stored"}
    sid = registry.add(
        "observation",
        f"peers:{ticker}",
        f"{ticker} peer set",
        app_path=f"/companies/{ticker}",
        tier=2,
        snippet=", ".join(f"{r['peer_ticker']} ({r['status']})" for r in rows)[:1500],
    )
    items = [
        {
            "ticker": r["peer_ticker"],
            "basis": r["basis"],
            "status": r["status"],
            "source": r["source"],
        }
        for r in rows
    ]
    return {
        "ticker": ticker,
        "source_id": sid,
        "peers": items,
        "page": env.page(items, 25),
        "note": "A 'suggested' peer is not confirmed by the owner.",
    }


# ------------------------------------------------------------------ funds


async def etf_breakdown(
    pool: asyncpg.Pool, registry: Registry, fund_isin: str, limit: Any
) -> dict[str, Any]:
    """The latest stored holdings snapshot of an ETF: top holdings and weights by sector and
    country (fractions of net assets)."""
    isin = str(fund_isin or "").upper()
    snap = await pool.fetchrow(
        "select id, fund_isin, fund_name, provider, as_of, holdings_count, equity_weight,"
        " warnings from public.etf_snapshot where fund_isin = $1"
        " order by as_of desc, fetched_at desc limit 1",
        isin,
    )
    if snap is None:
        return {"fund_isin": isin, "status": "missing", "reason": "no holdings snapshot stored"}
    top_n = env.clamp_limit(limit, default=10, maximum=25)
    top = await pool.fetch(
        "select name, ticker, isin, weight, sector, country from public.etf_holding"
        " where snapshot_id = $1 order by weight desc, name, isin limit $2",
        snap["id"],
        top_n,
    )
    groups = await pool.fetch(
        "select sector, country, weight from public.etf_holding where snapshot_id = $1",
        snap["id"],
    )
    by_sector: dict[str, float] = defaultdict(float)
    by_country: dict[str, float] = defaultdict(float)
    for g in groups:
        by_sector[g["sector"] or "unclassified"] += float(g["weight"])
        by_country[g["country"] or "unclassified"] += float(g["weight"])

    def _top(d: dict[str, float]) -> list[dict[str, Any]]:
        return [
            {"name": k, "weight": round(v, 4)}
            for k, v in sorted(d.items(), key=lambda kv: -kv[1])[:10]
        ]

    status = env.age_status(snap["as_of"], _today(), 45)
    sid = registry.add(
        "observation",
        f"etf:{isin}:{snap['as_of']}",
        f"{snap['fund_name'] or isin} holdings {snap['as_of']}",
        tier=1,
        snippet="; ".join(f"{h['name']} {float(h['weight']) * 100:.2f}%" for h in top[:8])[:1500],
        as_of=str(snap["as_of"]),
    )
    return {
        "fund_isin": isin,
        "fund_name": snap["fund_name"],
        "provider": snap["provider"],
        "source_id": sid,
        "as_of": env.datum(
            snap["as_of"].isoformat(), status=status, as_of=snap["as_of"], source_id=sid
        ),
        "holdings_count": snap["holdings_count"],
        "equity_weight": env.datum(snap["equity_weight"], unit="fraction", source_id=sid)
        if snap["equity_weight"] is not None
        else env.absent("missing", "no equity weight stored"),
        "top_holdings": [
            {
                "name": h["name"],
                "ticker": h["ticker"],
                "weight": env.datum(h["weight"], unit="fraction", source_id=sid),
                "sector": h["sector"],
                "country": h["country"],
            }
            for h in top
        ],
        "by_sector": _top(by_sector),
        "by_country": _top(by_country),
        "warnings": list(snap["warnings"] or [])[:5],
        "page": env.page(top, top_n, snap["holdings_count"]),
    }


async def fund_nav(
    pool: asyncpg.Pool, registry: Registry, owner: str, fund_isin: str, limit: Any
) -> dict[str, Any]:
    """NAV points the owner entered or the issuer sync stored for a fund, newest first."""
    isin = str(fund_isin or "").upper()
    rows_n = env.clamp_limit(limit, default=8, maximum=30)
    rows = await pool.fetch(
        "select as_of, nav, currency, source_url from public.fund_nav_point"
        " where owner_id = $1::uuid and fund_isin = $2 order by as_of desc, id desc limit $3",
        owner,
        isin,
        rows_n,
    )
    if not rows:
        return {"fund_isin": isin, "status": "missing", "reason": "no NAV point is stored"}
    latest = rows[0]
    sid = registry.add(
        "observation",
        latest["source_url"],
        f"{isin} NAV {latest['as_of']}",
        url=latest["source_url"],
        tier=2,
        snippet=f"NAV {latest['nav']:.6g} {latest['currency']} on {latest['as_of']}",
        as_of=str(latest["as_of"]),
    )
    status = env.age_status(latest["as_of"], _today(), NAV_STALE_DAYS)
    return {
        "fund_isin": isin,
        "source_id": sid,
        "latest": env.datum(
            latest["nav"],
            status=status,
            as_of=latest["as_of"],
            unit="per_share",
            currency=latest["currency"],
            source_id=sid,
        ),
        "points": [
            {"as_of": r["as_of"].isoformat(), "nav": env.plain(r["nav"]), "currency": r["currency"]}
            for r in rows
        ],
        "page": env.page(rows, rows_n),
    }


# ------------------------------------------------------------------ review queue


async def coverage_review(pool: asyncpg.Pool, limit: Any) -> dict[str, Any]:
    """Datasets and entities a provider does not cover or could not fetch (the review queue)."""
    rows_n = env.clamp_limit(limit, default=25, maximum=60)
    rows = await pool.fetch(
        "select provider, dataset, entity_key, state, reason, latest_period, checked_at"
        " from public.coverage_status where state <> 'available'"
        " order by checked_at desc limit $1",
        rows_n,
    )
    total = await pool.fetchval(
        "select count(*) from public.coverage_status where state <> 'available'"
    )
    items = [
        {
            "provider": r["provider"],
            "dataset": r["dataset"],
            "entity": r["entity_key"],
            "state": r["state"],
            "reason": r["reason"],
            "latest_period": env.plain(r["latest_period"]),
            "checked_at": env.plain(r["checked_at"]),
        }
        for r in rows
    ]
    return {
        "items": items,
        "page": env.page(items, rows_n, int(total or 0)),
        "note": "Each row is a gap, not a zero: the figure is not available from that provider.",
    }
