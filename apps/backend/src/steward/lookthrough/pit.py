"""Point-in-time (as-of) look-through (plan D6.8, cross-source contract item 6).

A query "as of D" only uses what was known by the end of day D: the portfolio snapshot dated
on or before D and, per fund, the newest stored holdings snapshot whose holdings date is on or
before D *and* that had been fetched by then (`fetched_at` is the time it became known to us;
a snapshot backfilled later never leaks into an earlier decision). The result says which
snapshot was used so a report can pin it (steward.lookthrough.retention).
"""

import datetime as dt
from dataclasses import dataclass

import asyncpg

from steward.config.jobs import JobsConfig
from steward.config.store import load_active
from steward.config.strategy import StrategyConfig
from steward.lookthrough.read import (
    LookThroughReport,
    PortfolioIn,
    PositionIn,
    SnapshotIn,
    compute_look_through,
    hydrate_snapshots,
)


def known_cutoff(as_of: dt.date) -> dt.datetime:
    """The first instant after day `as_of` (UTC): records fetched before it were known."""
    return dt.datetime.combine(as_of + dt.timedelta(days=1), dt.time.min, tzinfo=dt.UTC)


async def snapshot_metas_as_of(
    pool: asyncpg.Pool, isins: list[str], as_of: dt.date
) -> list[asyncpg.Record]:
    rows: list[asyncpg.Record] = await pool.fetch(
        "select distinct on (fund_isin) id, fund_isin, source, as_of, fetched_at, fund_name,"
        " country_basis, currency_basis, warnings from public.etf_snapshot"
        " where fund_isin = any($1::text[]) and as_of <= $2 and fetched_at < $3"
        " order by fund_isin, as_of desc, fetched_at desc",
        isins,
        as_of,
        known_cutoff(as_of),
    )
    return rows


async def snapshot_ids_as_of(
    pool: asyncpg.Pool, isins: list[str], as_of: dt.date
) -> dict[str, int]:
    return {r["fund_isin"]: r["id"] for r in await snapshot_metas_as_of(pool, isins, as_of)}


async def load_snapshots_as_of(
    pool: asyncpg.Pool, isins: list[str], as_of: dt.date
) -> dict[str, SnapshotIn]:
    return await hydrate_snapshots(pool, await snapshot_metas_as_of(pool, isins, as_of))


async def load_portfolio_as_of(pool: asyncpg.Pool, as_of: dt.date) -> PortfolioIn | None:
    account = await pool.fetchrow(
        "select snapshot_date, base_currency, account_currency, cash_base"
        " from public.account_snapshot where snapshot_date <= $1"
        " order by snapshot_date desc limit 1",
        as_of,
    )
    if account is None:
        return None
    strategy = await load_active(pool, "strategy", StrategyConfig)
    classifications = strategy.body.classifications if strategy else {}
    positions = await pool.fetch(
        "select p.ticker, p.value_base, p.instrument_currency, i.name, i.isin, i.security_type,"
        " i.home_country from public.position_snapshot p"
        " left join public.instrument i on i.t212_ticker = p.ticker"
        " where p.snapshot_date = $1 order by p.value_base desc",
        account["snapshot_date"],
    )
    return PortfolioIn(
        snapshot_date=account["snapshot_date"],
        base_currency=account["base_currency"],
        account_currency=account["account_currency"],
        cash_base=float(account["cash_base"]),
        positions=[
            PositionIn(
                ticker=p["ticker"],
                name=p["name"],
                isin=p["isin"],
                security_type=p["security_type"],
                home_country=p["home_country"],
                currency=p["instrument_currency"],
                value_base=float(p["value_base"]),
                strategy_sector=(classifications.get(p["ticker"]) or {}).get("sector"),
            )
            for p in positions
        ],
    )


@dataclass(frozen=True)
class AsOfLookThrough:
    as_of: dt.date
    report: LookThroughReport
    snapshot_ids: dict[str, int]


async def portfolio_look_through_as_of(
    pool: asyncpg.Pool, as_of: dt.date, *, top: int = 25
) -> AsOfLookThrough:
    """The look-through as it could have been computed at the end of day `as_of`. Staleness
    is measured against `as_of`, not today. Strategy sector buckets are the current ones."""
    stored = await load_active(pool, "jobs", JobsConfig)
    config = stored.body if stored else JobsConfig()
    settings = config.look_through
    portfolio = await load_portfolio_as_of(pool, as_of)
    metas = await snapshot_metas_as_of(pool, [f.isin for f in settings.funds], as_of)
    snapshots = await hydrate_snapshots(pool, metas)
    report = compute_look_through(
        portfolio,
        settings,
        snapshots,
        {},
        benchmark_symbols=config.prices.benchmark_symbols,
        today=as_of,
        top=top,
    )
    report.notes.append(
        f"Point-in-time view as of {as_of}: only snapshots fetched by then are used."
    )
    return AsOfLookThrough(
        as_of=as_of, report=report, snapshot_ids={m["fund_isin"]: m["id"] for m in metas}
    )
