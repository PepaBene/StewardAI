"""`etf_look_through` (weekly): the holdings of every configured fund the portfolio holds,
plus the funds that proxy a benchmark, from the issuers (steward.sources.etf_issuers).

Per fund, one step:
- Skip without a request when the stored snapshot is already as recent as the issuer can
  have (iShares: the previous business day; Vanguard: the last month end). Vanguard's small
  fund query gives the current as-of date first, so its 1.7 MB of holdings pages are only
  downloaded when that date moved; an iShares download whose as-of date equals the stored
  one is not stored again.
- A failed fetch (HTTP 400, a missing container, a timeout) is a recorded gap, never a
  failed job. Fallbacks, in order: the last stored snapshot when it is at most
  `cached_fallback_days` old; for iShares the full-fund SpreadsheetML download (no country,
  no ISIN); otherwise nothing, and the API labels the fund as not looked through.
- Snapshots keep the original payloads; only the newest `keep_snapshots` per fund are kept.

The outcome per fund (checked_at, ok, error, fallback) is kept in `sync_state`
('etf_look_through_status') for the API's labels.
"""

import datetime as dt
import json
from dataclasses import dataclass, field
from typing import Any

import asyncpg
import httpx

from steward import __version__
from steward.config.jobs import JobsConfig, LookThroughFund, LookThroughSettings
from steward.config.store import load_active
from steward.jobs.runner import JobContext
from steward.lookthrough.classify import region_of
from steward.sources.etf_issuers import (
    FundSnapshot,
    IssuerError,
    fetch_ishares,
    fetch_ishares_spreadsheet,
    fetch_vanguard_fund,
    fetch_vanguard_holdings,
    parse_vanguard,
    vanguard_as_of,
)

STATUS_KEY = "etf_look_through_status"


@dataclass
class LookThroughDeps:
    http: httpx.AsyncClient
    today: dt.date = field(default_factory=lambda: dt.datetime.now(dt.UTC).date())
    retry_delay: float = 2.0


def expected_as_of(provider: str, today: dt.date) -> dt.date:
    """The newest as-of date the issuer can have published by `today`: iShares publishes
    the previous business day's holdings (T+1), Vanguard month-end holdings."""
    if provider == "vanguard":
        return today.replace(day=1) - dt.timedelta(days=1)
    day = today - dt.timedelta(days=1)
    while day.weekday() >= 5:
        day -= dt.timedelta(days=1)
    return day


async def latest_snapshot(pool: asyncpg.Pool, isin: str) -> asyncpg.Record | None:
    row: asyncpg.Record | None = await pool.fetchrow(
        "select id, as_of, source, fetched_at from public.etf_snapshot where fund_isin = $1"
        " order by as_of desc, fetched_at desc limit 1",
        isin,
    )
    return row


async def store_snapshot(
    pool: asyncpg.Pool, fund: LookThroughFund, snap: FundSnapshot, job_id: int | None
) -> int | None:
    """Write the snapshot, its rows and breakdowns; None when that (fund, as-of, source)
    is already stored."""
    async with pool.acquire() as conn, conn.transaction():
        snapshot_id: int | None = await conn.fetchval(
            "insert into public.etf_snapshot (fund_isin, provider, portfolio_id, source, as_of,"
            " fund_name, fund_currency, country_basis, currency_basis, holdings_count,"
            " equity_weight, warnings, raw, raw_text, job_id) values ($1, $2, $3, $4, $5, $6,"
            " $7, $8, $9, $10, $11, $12, $13::jsonb, $14, $15)"
            " on conflict (fund_isin, as_of, source) do nothing returning id",
            fund.isin,
            snap.provider,
            snap.portfolio_id,
            snap.source,
            snap.as_of,
            snap.fund_name,
            snap.fund_currency,
            snap.country_basis,
            snap.currency_basis,
            sum(1 for r in snap.rows if r.asset_class == "equity"),
            snap.equity_weight,
            snap.warnings,
            json.dumps(snap.raw) if snap.raw is not None else None,
            snap.raw_text,
            job_id,
        )
        if snapshot_id is None:
            return None
        # COPY is not allowed on a table with row-level security: a pipelined insert.
        await conn.executemany(
            "insert into public.etf_holding (snapshot_id, row_index, name, isin, ticker, sedol,"
            " asset_class, security_type, weight, sector_raw, sector, country_raw, country,"
            " currency, currency_derived, exchange, market_value) values ($1, $2, $3, $4, $5,"
            " $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17)",
            [
                (
                    snapshot_id,
                    i,
                    r.name,
                    r.isin,
                    r.ticker,
                    r.sedol,
                    r.asset_class,
                    r.security_type,
                    r.weight,
                    r.sector_raw,
                    r.sector,
                    r.country_raw,
                    r.country,
                    r.currency,
                    r.currency_derived,
                    r.exchange,
                    r.market_value,
                )
                for i, r in enumerate(snap.rows)
            ],
        )
        # One row per (scheme, label): a repeated label keeps its first row.
        seen: set[tuple[str, str]] = set()
        breakdowns = []
        for b in snap.breakdowns:
            if (b.scheme, b.label) in seen:
                continue
            seen.add((b.scheme, b.label))
            breakdowns.append(
                (
                    snapshot_id,
                    b.dimension,
                    b.scheme,
                    b.code,
                    b.label,
                    b.fund_weight,
                    b.benchmark_weight,
                    b.as_of,
                )
            )
        await conn.executemany(
            "insert into public.etf_breakdown (snapshot_id, dimension, scheme, code, label,"
            " fund_weight, benchmark_weight, as_of) values ($1, $2, $3, $4, $5, $6, $7, $8)",
            breakdowns,
        )
    return snapshot_id


async def prune(pool: asyncpg.Pool, isin: str, keep: int) -> int:
    deleted: str = await pool.execute(
        "delete from public.etf_snapshot where fund_isin = $1 and id not in (select id from"
        " public.etf_snapshot where fund_isin = $1 order by as_of desc, fetched_at desc"
        " limit $2)",
        isin,
        keep,
    )
    return int(deleted.split()[-1])


async def funds_to_fetch(
    pool: asyncpg.Pool, settings: LookThroughSettings, benchmark_symbols: list[str]
) -> list[tuple[LookThroughFund, list[str]]]:
    """Configured funds held in the latest snapshot, and those proxying a benchmark, each
    with why it is fetched."""
    held = {
        r["isin"]: r["t212_ticker"]
        for r in await pool.fetch(
            "select i.isin, i.t212_ticker from public.position_snapshot p"
            " join public.instrument i on i.t212_ticker = p.ticker"
            " where p.snapshot_date = (select max(snapshot_date) from public.account_snapshot)"
            " and i.isin is not null"
        )
    }
    out: list[tuple[LookThroughFund, list[str]]] = []
    for fund in settings.funds:
        reasons = []
        if fund.isin in held:
            reasons.append(f"held as {held[fund.isin]}")
        reasons += [
            f"benchmark proxy for {p.symbol}"
            for p in settings.benchmark_proxies
            if p.fund_isin == fund.isin and p.symbol in benchmark_symbols
        ]
        if reasons:
            out.append((fund, reasons))
    return out


async def _status(pool: asyncpg.Pool) -> dict[str, Any]:
    raw = await pool.fetchval("select value from public.sync_state where key = $1", STATUS_KEY)
    return dict(json.loads(raw)) if raw else {}


async def _save_status(pool: asyncpg.Pool, value: dict[str, Any]) -> None:
    await pool.execute(
        "insert into public.sync_state (key, value) values ($1, $2::jsonb)"
        " on conflict (key) do update set value = excluded.value",
        STATUS_KEY,
        json.dumps(value, default=str),
    )


async def _fetch_primary(
    deps: LookThroughDeps,
    fund: LookThroughFund,
    settings: LookThroughSettings,
    stored_as_of: dt.date | None,
) -> FundSnapshot | dt.date:
    """A new snapshot, or the (unchanged) as-of date the issuer reports."""
    if fund.provider == "ishares":
        return await fetch_ishares(
            deps.http,
            fund.portfolio_id,
            api_url=settings.ishares_api_url,
            site=settings.ishares_site,
            locale=settings.ishares_locale,
            retry_delay=deps.retry_delay,
        )
    fund_body = await fetch_vanguard_fund(
        deps.http,
        fund.portfolio_id,
        url=settings.vanguard_graphql_url,
        consumer_id=settings.vanguard_consumer_id,
        retry_delay=deps.retry_delay,
    )
    current = vanguard_as_of(fund_body)
    if current is not None and stored_as_of is not None and current <= stored_as_of:
        return current
    pages = await fetch_vanguard_holdings(
        deps.http,
        fund.portfolio_id,
        url=settings.vanguard_graphql_url,
        consumer_id=settings.vanguard_consumer_id,
        retry_delay=deps.retry_delay,
    )
    return parse_vanguard(fund_body, pages, fund.portfolio_id)


async def collect_fund(
    pool: asyncpg.Pool,
    deps: LookThroughDeps,
    fund: LookThroughFund,
    settings: LookThroughSettings,
    job_id: int | None,
    *,
    force: bool = False,
) -> dict[str, Any]:
    """Fetch one fund (or skip it); the step detail. Never raises for a source failure."""
    stored = await latest_snapshot(pool, fund.isin)
    stored_as_of: dt.date | None = stored["as_of"] if stored else None
    detail: dict[str, Any] = {
        "fund": fund.label,
        "isin": fund.isin,
        "provider": fund.provider,
        "stored_as_of": stored_as_of,
    }
    expected = expected_as_of(fund.provider, deps.today)
    # A snapshot from the spreadsheet fallback is replaced as soon as the JSON works again.
    primary_source = "vanguard_graphql" if fund.provider == "vanguard" else "ishares_json"
    if (
        not force
        and stored is not None
        and stored["source"] == primary_source
        and stored_as_of is not None
        and stored_as_of >= expected
    ):
        detail.update(outcome="skipped", reason=f"stored as of {stored_as_of} is current")
        return detail

    error: str | None = None
    snap: FundSnapshot | None = None
    try:
        result = await _fetch_primary(deps, fund, settings, None if force else stored_as_of)
        if isinstance(result, dt.date):
            detail.update(outcome="unchanged", issuer_as_of=result)
            return detail
        snap = result
    except IssuerError as exc:
        error = str(exc)
    except httpx.HTTPError as exc:
        error = f"{type(exc).__name__}: {exc}"

    if snap is None:
        detail.update(outcome="gap", error=error)
        cache_age = (deps.today - stored_as_of).days if stored_as_of else None
        if cache_age is not None and cache_age <= settings.cached_fallback_days:
            detail["fallback"] = "cached"
            return detail
        if fund.provider == "ishares" and settings.ishares_spreadsheet_fallback:
            try:
                snap = await fetch_ishares_spreadsheet(
                    deps.http,
                    fund.portfolio_id,
                    api_url=settings.ishares_api_url,
                    site=settings.ishares_site,
                    locale=settings.ishares_locale,
                    retry_delay=deps.retry_delay,
                )
                detail["fallback"] = "spreadsheet"
            except (IssuerError, httpx.HTTPError) as exc:
                detail["fallback_error"] = str(exc)
        if snap is None:
            detail["fallback"] = "cached" if stored is not None else "none"
            return detail

    snapshot_id = await store_snapshot(pool, fund, snap, job_id)
    detail.update(
        outcome=detail.get("outcome", "stored") if snapshot_id else "unchanged",
        as_of=snap.as_of,
        source=snap.source,
        rows=len(snap.rows),
        equity_weight=float(snap.equity_weight),
        warnings=snap.warnings,
        unmapped_regions=sorted(
            {r.country for r in snap.rows if r.country and region_of(r.country) is None}
        ),
    )
    if snapshot_id:
        detail["snapshot_id"] = snapshot_id
        detail["pruned"] = await prune(pool, fund.isin, settings.keep_snapshots)
    return detail


async def run_look_through(
    ctx: JobContext, deps: LookThroughDeps, config: JobsConfig, *, force: bool = False
) -> None:
    settings = config.look_through
    async with ctx.step("funds") as step:
        todo = await funds_to_fetch(ctx.pool, settings, config.prices.benchmark_symbols)
        step.detail.update(
            funds=[{"fund": f.label, "isin": f.isin, "why": why} for f, why in todo],
            configured=len(settings.funds),
        )
    status = await _status(ctx.pool)
    for fund, _why in todo:
        async with ctx.step(f"fund:{fund.label}") as step:
            detail = await collect_fund(ctx.pool, deps, fund, settings, ctx.job_id, force=force)
            step.detail.update(detail)
            previous = status.get(fund.isin, {})
            entry = {
                "checked_at": dt.datetime.now(dt.UTC),
                "outcome": detail["outcome"],
                "ok": detail["outcome"] != "gap" or detail.get("fallback") == "spreadsheet",
                "error": detail.get("error"),
                "fallback": detail.get("fallback"),
                "job_id": ctx.job_id,
            }
            if detail["outcome"] == "gap":
                entry["last_success_at"] = previous.get("last_success_at")
            else:
                entry["last_success_at"] = entry["checked_at"]
            status[fund.isin] = entry
            await _save_status(ctx.pool, status)


async def etf_look_through_handler(ctx: JobContext) -> None:
    stored = await load_active(ctx.pool, "jobs", JobsConfig)
    await ctx.record_config_version(stored.id if stored else None)
    config = stored.body if stored else JobsConfig()
    if not config.look_through.enabled:
        async with ctx.step("disabled") as step:
            step.detail["reason"] = "look_through.enabled is off in the Jobs config"
        return
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(60.0, connect=15.0),
        headers={"User-Agent": f"StewardAI/{__version__} (personal portfolio tool)"},
        follow_redirects=True,
    ) as http:
        await run_look_through(
            ctx, LookThroughDeps(http=http), config, force=bool(ctx.params.get("force"))
        )
