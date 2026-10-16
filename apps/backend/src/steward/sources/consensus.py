# ruff: noqa: E501 - detail text stays on one line each
"""Analyst consensus snapshots (insight plan I0.2, I0.4).

Three free-plan candidates for a consensus price target are tested and, where they answer, read
weekly into `consensus_snapshot`:

- FMP `/stable/price-target-consensus` (high, low, consensus, median; no analyst count);
- Finnhub `/api/v1/stock/price-target` (documented as premium; a plan denial latches it paid-only);
- Twelve Data `/price_target` (documented for higher plans; same latch).

The FMP row also carries the next fiscal year's EPS and revenue consensus and the analyst count
already stored in `forward_observation` by the `fmp_company` job. An endpoint that is latched,
denied or has nothing for the symbol writes a row with that access state and no figures: absence
is recorded, never a zero, and no synthetic consensus is ever made. Field names follow the
providers' public documentation and are parsed defensively; a body that does not have them is
`unavailable` with the reason.

The target currency is not stated by any of the three; it is recorded as the listing's trading
currency, and the snapshot's `detail` says so.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

import asyncpg
import httpx

from steward import __version__
from steward.analytics.versions import version
from steward.jobs.runner import JobContext
from steward.sources.free_market import FreeSourceError
from steward.sources.governor import Budget, HardBlocked, QuotaExceeded

FMP_TARGET_URL = "https://financialmodelingprep.com/stable/price-target-consensus"
FINNHUB_TARGET_URL = "https://finnhub.io/api/v1/stock/price-target"
TWELVE_TARGET_URL = "https://api.twelvedata.com/price_target"
HOLDINGS_PER_RUN = 40
FINNHUB_BUDGET = Budget(per_minute=Decimal(20), daily=Decimal(200))
TWELVE_BUDGET = Budget(per_minute=Decimal(4), daily=Decimal(40))
STATES = ("complete", "partial", "unavailable", "paid_only")


@dataclass
class Target:
    mean: Decimal | None = None
    median: Decimal | None = None
    high: Decimal | None = None
    low: Decimal | None = None
    analysts: int | None = None
    updated: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def any(self) -> bool:
        return any(v is not None for v in (self.mean, self.median, self.high, self.low))


def _dec(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        out = Decimal(str(value))
    except InvalidOperation:
        return None
    return out if out.is_finite() and out > 0 else None


def _int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        out = int(str(value))
    except (TypeError, ValueError):
        return None
    return out if out >= 0 else None


def parse_fmp_target(body: object) -> Target | None:
    rows = body if isinstance(body, list) else [body] if isinstance(body, dict) else []
    row = next((r for r in rows if isinstance(r, dict)), None)
    if row is None:
        return None
    t = Target(
        mean=_dec(row.get("targetConsensus")),
        median=_dec(row.get("targetMedian")),
        high=_dec(row.get("targetHigh")),
        low=_dec(row.get("targetLow")),
        raw=dict(row),
    )
    return t if t.any else None


def parse_finnhub_target(body: object) -> Target | None:
    if not isinstance(body, dict):
        return None
    t = Target(
        mean=_dec(body.get("targetMean")),
        median=_dec(body.get("targetMedian")),
        high=_dec(body.get("targetHigh")),
        low=_dec(body.get("targetLow")),
        analysts=_int(body.get("numberAnalysts") or body.get("numberOfAnalysts")),
        updated=str(body.get("lastUpdated")) if body.get("lastUpdated") else None,
        raw=dict(body),
    )
    return t if t.any else None


def parse_twelve_target(body: object) -> Target | None:
    if not isinstance(body, dict):
        return None
    pt = body.get("price_target")
    if not isinstance(pt, dict):
        return None
    t = Target(
        mean=_dec(pt.get("average")),
        median=_dec(pt.get("median")),
        high=_dec(pt.get("high")),
        low=_dec(pt.get("low")),
        raw=dict(body),
    )
    return t if t.any else None


@dataclass
class Snapshot:
    ticker: str
    source: str
    access_state: str
    detail: str
    target: Target | None = None
    analyst_count: int | None = None
    eps_next_fy: Decimal | None = None
    revenue_next_fy: Decimal | None = None
    fiscal_period: str | None = None
    estimate_currency: str | None = None


@dataclass
class Holding:
    ticker: str
    fmp_symbol: str | None
    us_symbol: str | None
    listing_symbol: str | None
    listing_currency: str | None
    price: Decimal | None
    price_currency: str | None


async def _holdings(pool: asyncpg.Pool) -> list[Holding]:
    from steward.config.holding_profiles import HoldingProfilesConfig
    from steward.config.store import load_active
    from steward.reference.identity import holding_tickers

    tickers = await holding_tickers(pool)
    stored = await load_active(pool, "holding_profiles", HoldingProfilesConfig)
    profiles = stored.body if stored else HoldingProfilesConfig()
    rows = {
        r["ticker"]: r
        for r in await pool.fetch(
            "select u.ticker, u.currency, u.mic, l.ticker as listing_ticker, p.current_price, p.instrument_currency"
            " from public.investment_universe u"
            " left join public.security_listing l on l.id = u.listing_id"
            " left join public.position_snapshot p on p.ticker = u.ticker and p.snapshot_date ="
            "  (select max(snapshot_date) from public.position_snapshot)"
            " where u.ticker = any($1::text[])",
            tickers,
        )
    }
    out: list[Holding] = []
    for t in tickers:
        profile = profiles.for_ticker(t)
        r = rows.get(t)
        us = profile.sec_ticker if profile and profile.sec_ticker else None
        if (
            us is None
            and r is not None
            and (r["mic"] or "") in ("XNYS", "XNAS", "ARCX", "BATS", "XASE")
        ):
            us = r["listing_ticker"]
        out.append(
            Holding(
                ticker=t,
                fmp_symbol=(profile.fmp_symbol or profile.sec_ticker) if profile else None,
                us_symbol=us,
                listing_symbol=r["listing_ticker"] if r else None,
                listing_currency=r["currency"] if r else None,
                price=r["current_price"] if r else None,
                price_currency=r["instrument_currency"] if r else None,
            )
        )
    return out


async def _estimates(
    pool: asyncpg.Pool, ticker: str, today: dt.date
) -> tuple[Decimal | None, Decimal | None, int | None, str | None, str | None]:
    """Next fiscal year's EPS and revenue consensus already stored from FMP, with its count."""
    rows = await pool.fetch(
        "select distinct on (metric) metric, value, analyst_count, fiscal_period, currency"
        " from public.forward_observation where ticker = $1 and kind = 'published_analyst_consensus'"
        " and period_end > $2 order by metric, period_end, known_at desc",
        ticker,
        today,
    )
    eps = rev = None
    count = None
    period = currency = None
    for r in rows:
        if r["metric"] == "earnings_per_share":
            eps, count, period, currency = (
                r["value"],
                r["analyst_count"],
                r["fiscal_period"],
                r["currency"],
            )
        elif r["metric"] == "revenue":
            rev = r["value"]
            count = count if count is not None else r["analyst_count"]
            period = period or r["fiscal_period"]
    return eps, rev, count, period, currency


def _state_for(target: Target | None, has_estimates: bool) -> str:
    if target is not None and target.any:
        return "complete" if has_estimates else "partial"
    return "partial" if has_estimates else "unavailable"


async def _fmp(
    pool: asyncpg.Pool,
    http: httpx.AsyncClient,
    h: Holding,
    api_key: str | None,
    settings: Any,
    today: dt.date,
) -> Snapshot:
    from steward.sources.fmp import _fetch_governed, endpoint_budget
    from steward.sources.governor import PostgresRequestGovernor

    eps, rev, count, period, ccy = await _estimates(pool, h.ticker, today)
    snap = Snapshot(h.ticker, "fmp", "unavailable", "", analyst_count=count, eps_next_fy=eps,
                    revenue_next_fy=rev, fiscal_period=period, estimate_currency=ccy)  # fmt: skip
    has_est = eps is not None or rev is not None
    if not api_key:
        snap.access_state = _state_for(None, has_est)
        snap.detail = "FMP_API_KEY is not set; estimates from stored observations only"
        return snap
    if not h.fmp_symbol:
        snap.access_state = _state_for(None, has_est)
        snap.detail = (
            "no FMP symbol mapped for this holding (holding profile fmp_symbol or sec_ticker)"
        )
        return snap
    try:
        body = await _fetch_governed(
            pool, http, PostgresRequestGovernor(pool), endpoint="price_target_consensus",
            url=FMP_TARGET_URL, params={"symbol": h.fmp_symbol}, api_key=api_key,
            budget=endpoint_budget(settings),
        )  # fmt: skip
    except HardBlocked as exc:
        snap.access_state = "paid_only"
        snap.detail = str(exc)[:200]
        return snap
    except (QuotaExceeded, RuntimeError, FreeSourceError) as exc:
        snap.access_state = _state_for(None, has_est)
        snap.detail = f"price target not read: {type(exc).__name__}"
        return snap
    target = parse_fmp_target(body) if body is not None else None
    snap.target = target
    snap.access_state = _state_for(target, has_est)
    snap.detail = (
        "price target and estimates read" if target and has_est
        else "price target read; no stored estimates" if target
        else "the free plan returned no price target for this symbol"
    )  # fmt: skip
    return snap


async def _governed_target(
    pool: asyncpg.Pool, http: httpx.AsyncClient, *, provider: str, endpoint: str, url: str,
    params: dict[str, Any], headers: dict[str, str] | None, budget: Budget, denial: Any = None,
) -> tuple[str, Any, str]:  # fmt: skip
    """(state, body, detail) of one governed request; never raises for access outcomes."""
    from steward.sources.governed_http import governed_get

    try:
        response = await governed_get(pool, http, provider=provider, endpoint=endpoint, url=url,
                                      params=params, headers=headers, budget=budget, body_denial=denial)  # fmt: skip
    except HardBlocked as exc:
        return "paid_only", None, str(exc)[:200]
    except QuotaExceeded as exc:
        return "unavailable", None, f"quota: {exc}"[:200]
    except (RuntimeError, FreeSourceError, httpx.HTTPError) as exc:
        return "unavailable", None, f"{type(exc).__name__}: {exc}"[:200]
    if response is None:
        return "unavailable", None, "nothing for this symbol (404)"
    try:
        return "complete", response.json(), "read"
    except ValueError:
        return "unavailable", None, "response was not JSON"


async def _finnhub(
    pool: asyncpg.Pool, http: httpx.AsyncClient, h: Holding, api_key: str | None
) -> Snapshot:
    snap = Snapshot(h.ticker, "finnhub", "unavailable", "")
    if not api_key:
        snap.detail = "FINNHUB_API_KEY is not set"
        return snap
    if not h.us_symbol:
        snap.detail = "no US symbol: the free Finnhub plan covers US listings only"
        return snap
    state, body, detail = await _governed_target(
        pool, http, provider="finnhub", endpoint="price_target", url=FINNHUB_TARGET_URL,
        params={"symbol": h.us_symbol}, headers={"X-Finnhub-Token": api_key}, budget=FINNHUB_BUDGET,
    )  # fmt: skip
    snap.target = parse_finnhub_target(body) if body is not None else None
    snap.analyst_count = snap.target.analysts if snap.target else None
    snap.access_state = (
        "complete" if snap.target else ("paid_only" if state == "paid_only" else "unavailable")
    )
    snap.detail = detail if not snap.target else "price target read"
    return snap


async def _twelve(
    pool: asyncpg.Pool, http: httpx.AsyncClient, h: Holding, api_key: str | None
) -> Snapshot:
    from steward.sources.twelvedata import classify_envelope

    snap = Snapshot(h.ticker, "twelve_data", "unavailable", "")
    if not api_key:
        snap.detail = "TWELVE_DATA_API_KEY is not set"
        return snap
    if not h.listing_symbol:
        snap.detail = "no confirmed listing symbol"
        return snap
    state, body, detail = await _governed_target(
        pool, http, provider="twelve_data", endpoint="price_target", url=TWELVE_TARGET_URL,
        params={"symbol": h.listing_symbol}, headers={"Authorization": f"apikey {api_key}"},
        budget=TWELVE_BUDGET, denial=classify_envelope,
    )  # fmt: skip
    snap.target = parse_twelve_target(body) if body is not None else None
    snap.access_state = (
        "complete" if snap.target else ("paid_only" if state == "paid_only" else "unavailable")
    )
    snap.detail = detail if not snap.target else "price target read"
    return snap


async def store_snapshot(pool: asyncpg.Pool, s: Snapshot, h: Holding, today: dt.date) -> bool:
    t = s.target
    price_ok = (
        h.price is not None
        and h.price_currency
        and h.listing_currency
        and h.price_currency.upper() == h.listing_currency.upper()
    )
    detail = s.detail
    if t is not None:
        detail = f"{detail}; target currency recorded as the listing currency ({h.listing_currency or 'unknown'})"
    status = await pool.execute(
        """insert into public.consensus_snapshot
           (ticker, as_of, source, analyst_count, target_mean, target_median, target_high, target_low,
            target_currency, eps_next_fy, revenue_next_fy, fiscal_period, estimate_currency,
            price_at_snapshot, access_state, detail, raw)
           values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17::jsonb)
           on conflict (ticker, as_of, source) do nothing""",
        h.ticker, today, s.source, s.analyst_count,
        t.mean if t else None, t.median if t else None, t.high if t else None, t.low if t else None,
        h.listing_currency if t else None, s.eps_next_fy, s.revenue_next_fy, s.fiscal_period,
        s.estimate_currency if s.estimate_currency and len(s.estimate_currency) == 3 else None,
        h.price if price_ok else None, s.access_state, detail[:500],
        json.dumps({"provider": t.raw if t else None, "method_version": version("consensus_snapshot")}, default=str),
    )  # fmt: skip
    return str(status).endswith(" 1")


async def consensus_snapshot_handler(ctx: JobContext) -> None:
    """Weekly: one row per holding and source, figures or the access state that stopped them."""
    from steward.config.jobs import JobsConfig
    from steward.config.store import load_active
    from steward.settings import get_settings

    settings = get_settings()
    async with ctx.step("consensus_snapshot") as step:
        jobs = await load_active(ctx.pool, "jobs", JobsConfig)
        providers = (jobs.body if jobs else JobsConfig()).providers
        fmp_key = settings.fmp_api_key.get_secret_value() if settings.fmp_api_key else None
        fin_key = settings.finnhub_api_key.get_secret_value() if settings.finnhub_api_key else None
        td_key = (
            settings.twelve_data_api_key.get_secret_value()
            if settings.twelve_data_api_key
            else None
        )
        today = dt.date.today()
        written: dict[str, dict[str, str]] = {}
        async with httpx.AsyncClient(
            timeout=30, headers={"User-Agent": f"StewardAI/{__version__}"}
        ) as http:
            for h in (await _holdings(ctx.pool))[:HOLDINGS_PER_RUN]:
                states: dict[str, str] = {}
                for snap in (
                    await _fmp(ctx.pool, http, h, fmp_key, providers.get("fmp"), today),
                    await _finnhub(ctx.pool, http, h, fin_key),
                    await _twelve(ctx.pool, http, h, td_key),
                ):
                    await store_snapshot(ctx.pool, snap, h, today)
                    states[snap.source] = snap.access_state
                written[h.ticker] = states
        step.detail["snapshots"] = written


# ------------------------------------------------------------------ access tests (I0.2)

PROBES = (
    ("yahoo", "chart_dividends"),
    ("fmp", "price_target_consensus"),
    ("finnhub", "price_target"),
    ("twelve_data", "price_target"),
)


async def access_verdicts(pool: asyncpg.Pool) -> list[dict[str, Any]]:
    rows = await pool.fetch(
        "select provider, endpoint, state, tested_at, detail, status_code, updated_at"
        " from public.provider_access where (provider, endpoint) in"
        " (('yahoo','chart_dividends'),('fmp','price_target_consensus'),('finnhub','price_target'),"
        "  ('twelve_data','price_target'),('twelve_data','corporate_actions'),('fmp','analyst_estimates'))"
        " order by provider, endpoint"
    )
    return [dict(r) for r in rows]


async def insight_access_probe_handler(ctx: JobContext) -> None:
    """One request per candidate endpoint with a held symbol, so each has a dated verdict in
    `provider_access`. A latched paid-only endpoint is not called again."""
    from steward.config.jobs import JobsConfig
    from steward.config.store import load_active
    from steward.settings import get_settings
    from steward.sources.yahoo import collect_yahoo_dividends

    settings = get_settings()
    async with ctx.step("insight_access_probe") as step:
        holdings = await _holdings(ctx.pool)
        results: dict[str, str] = {}
        jobs = await load_active(ctx.pool, "jobs", JobsConfig)
        providers = (jobs.body if jobs else JobsConfig()).providers
        async with httpx.AsyncClient(
            timeout=30, headers={"User-Agent": f"StewardAI/{__version__}"}
        ) as http:
            us = next((h for h in holdings if h.us_symbol), None)
            fmp_h = next((h for h in holdings if h.fmp_symbol), None)
            listed = next((h for h in holdings if h.listing_symbol), None)
            fmp_key = settings.fmp_api_key.get_secret_value() if settings.fmp_api_key else None
            fin_key = (
                settings.finnhub_api_key.get_secret_value() if settings.finnhub_api_key else None
            )
            td_key = (
                settings.twelve_data_api_key.get_secret_value()
                if settings.twelve_data_api_key
                else None
            )
            if fmp_h:
                results["fmp.price_target_consensus"] = (
                    await _fmp(
                        ctx.pool, http, fmp_h, fmp_key, providers.get("fmp"), dt.date.today()
                    )
                ).access_state
            if us:
                results["finnhub.price_target"] = (
                    await _finnhub(ctx.pool, http, us, fin_key)
                ).access_state
            if listed:
                results["twelve_data.price_target"] = (
                    await _twelve(ctx.pool, http, listed, td_key)
                ).access_state
            target = await _yahoo_target(ctx.pool, holdings)
            if target is not None:
                listing_id, symbol, ysym = target
                try:
                    got = await collect_yahoo_dividends(
                        ctx.pool, http, listing_id=listing_id, symbol=symbol, yahoo_symbol=ysym
                    )
                    results["yahoo.chart_dividends"] = f"available ({got['proposals']} events)"
                except Exception as exc:  # the verdict is what provider_access recorded
                    results["yahoo.chart_dividends"] = f"{type(exc).__name__}"
        step.detail["probes"] = results
        step.detail["verdicts"] = [
            {**v, "tested_at": str(v.get("tested_at")), "updated_at": str(v.get("updated_at"))}
            for v in await access_verdicts(ctx.pool)
        ]


async def _yahoo_target(pool: asyncpg.Pool, holdings: list[Holding]) -> tuple[int, str, str] | None:
    from steward.sources.yahoo import yahoo_symbol_for

    for h in holdings:
        got = await yahoo_symbol_for(pool, h.ticker)
        if got is not None:
            return got
    return None
