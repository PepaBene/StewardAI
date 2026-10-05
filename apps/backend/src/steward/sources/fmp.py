"""Financial Modeling Prep (FMP) free tier: company profile (sector, industry, country,
exchange) and annual analyst estimates (D4.4).

Only the two free-gated `/stable` endpoints that the Phase 0 manifest lists are used
(`profile`, `analyst-estimates`); the sector-performance snapshot is a market statistic, not a
holding fact, and is not collected here. The free key allows 250 requests a day for the whole
account, and the ledger keeps one daily counter per endpoint, so each endpoint is capped at half
of that (`ENDPOINT_DAILY_CAP`): the two together can never pass 250.

Payload field names follow FMP's public `/stable` documentation and have NOT been checked against
a live response (no key available when this was written); see tests/test_fmp.py. A symbol the
free plan does not cover answers 402 (premium) or an empty list; both are recorded as coverage
gaps, never as zeros. A figure FMP omits is stored as null (or not at all), never as 0.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

import asyncpg
import httpx

from steward.free_sources import ForwardValue, store_forward_value
from steward.graph import provider_gate
from steward.sources.base import AccessResult, AccessState, Priority
from steward.sources.company_research import (
    _gate,
    _record_http_failure,
    store_research_observation,
)
from steward.sources.free_market import FreeSourceError
from steward.sources.governor import Budget, PostgresRequestGovernor, QuotaExceeded
from steward.sources.store import CoverageUpdate, record_access, update_coverage

FMP_BASE = "https://financialmodelingprep.com/stable"
PROFILE_URL = f"{FMP_BASE}/profile"
ESTIMATES_URL = f"{FMP_BASE}/analyst-estimates"
# The free account allowance is 250 requests/day shared by both endpoints; each endpoint's own
# ledger counter may use at most half.
ACCOUNT_DAILY_QUOTA = 250
ENDPOINT_DAILY_CAP = ACCOUNT_DAILY_QUOTA // 2
ESTIMATE_YEARS = 4
PROFILE_TEXT_FIELDS = ("sector", "industry", "country", "exchange", "companyName", "currency")


@dataclass(frozen=True, slots=True)
class FmpProfile:
    symbol: str
    fields: dict[str, str]  # only fields FMP actually sent, non-empty


@dataclass(frozen=True, slots=True)
class FmpEstimate:
    fiscal_year: int
    period_end: dt.date
    metric: str  # "earnings_per_share" | "revenue"
    value: Decimal | None
    low: Decimal | None
    high: Decimal | None
    analyst_count: int | None
    raw: dict[str, Any] = field(default_factory=dict)


def _decimal(value: object) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def _count(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return int(value) if value >= 0 and float(value).is_integer() else None


def parse_profile(payload: object, symbol: str) -> FmpProfile | None:
    """The profile row for `symbol`, or None when FMP has none (empty list). A row for another
    symbol is rejected rather than attributed to the holding."""
    if not isinstance(payload, list):
        raise FreeSourceError("FMP profile schema changed")
    row = next((r for r in payload if isinstance(r, dict)), None)
    if row is None:
        return None
    if str(row.get("symbol", "")).upper() != symbol.upper():
        return None
    fields = {
        name: " ".join(str(row[name]).split())
        for name in PROFILE_TEXT_FIELDS
        if isinstance(row.get(name), str) and str(row[name]).strip()
    }
    return FmpProfile(symbol.upper(), fields) if fields else None


def parse_estimates(payload: object, symbol: str) -> list[FmpEstimate]:
    """Annual consensus EPS and revenue per fiscal year. An analyst count FMP does not send
    stays None and the figure is dropped by the writer (a consensus needs its count)."""
    if not isinstance(payload, list):
        raise FreeSourceError("FMP analyst-estimates schema changed")
    out: list[FmpEstimate] = []
    for row in payload:
        if not isinstance(row, dict) or str(row.get("symbol", symbol)).upper() != symbol.upper():
            continue
        try:
            period_end = dt.date.fromisoformat(str(row.get("date")))
        except ValueError:
            continue
        for metric, avg, low, high, count in (
            ("earnings_per_share", "epsAvg", "epsLow", "epsHigh", "numAnalystsEps"),
            ("revenue", "revenueAvg", "revenueLow", "revenueHigh", "numAnalystsRevenue"),
        ):
            value, lo, hi = _decimal(row.get(avg)), _decimal(row.get(low)), _decimal(row.get(high))
            if value is None and lo is None and hi is None:
                continue
            out.append(
                FmpEstimate(
                    fiscal_year=period_end.year,
                    period_end=period_end,
                    metric=metric,
                    value=value,
                    low=lo,
                    high=hi,
                    analyst_count=_count(row.get(count)),
                    raw={k: row.get(k) for k in (avg, low, high, count) if k in row},
                )
            )
    return out


async def _get(http: httpx.AsyncClient, url: str, params: dict[str, Any], api_key: str) -> Any:
    try:
        response = await http.get(url, params={**params, "apikey": api_key})
    except httpx.HTTPError as exc:
        # The key is a query parameter: never let the request URL reach a log or job detail.
        raise FreeSourceError(
            f"FMP request failed ({type(exc).__name__})", provider_charged=False
        ) from exc
    if response.status_code != 200:
        raise _HttpFailure(response)
    try:
        body = response.json()
    except ValueError as exc:
        raise FreeSourceError("FMP sent no JSON") from exc
    if isinstance(body, dict) and (body.get("Error Message") or body.get("error")):
        raise FreeSourceError(str(body.get("Error Message") or body.get("error"))[:200], 200)
    return body


class _HttpFailure(FreeSourceError):
    def __init__(self, response: httpx.Response) -> None:
        super().__init__(f"FMP returned HTTP {response.status_code}", response.status_code)
        self.response = response


def endpoint_budget(provider_settings: Any) -> Budget:
    from steward.verify_sources.budgets import capped_settings_budget

    return capped_settings_budget(provider_settings, ENDPOINT_DAILY_CAP)


async def _fetch_governed(
    pool: asyncpg.Pool,
    http: httpx.AsyncClient,
    governor: PostgresRequestGovernor,
    *,
    endpoint: str,
    url: str,
    params: dict[str, Any],
    api_key: str,
    budget: Budget,
) -> Any:
    """Gate, reserve one credit (background priority: never starves an owner request), call,
    and record the typed outcome. 402 (premium symbol) is a coverage gap, not a block."""
    await _gate(pool, "fmp", endpoint)
    try:
        await governor.reserve("fmp", endpoint, Decimal(1), budget, priority=Priority.BACKGROUND)
    except QuotaExceeded:
        await record_access(
            pool, "fmp", endpoint, AccessResult(AccessState.QUOTA_EXHAUSTED, "free budget used")
        )
        raise
    try:
        body = await _get(http, url, params, api_key)
    except _HttpFailure as exc:
        if exc.status_code == 402:
            # The free plan excludes this symbol; the endpoint itself stays usable.
            await record_access(
                pool,
                "fmp",
                endpoint,
                AccessResult(AccessState.AVAILABLE, "symbol excluded by the free plan"),
                status_code=402,
                credits=1,
            )
            return None
        await _record_http_failure(pool, "fmp", endpoint, exc.response, "FMP request failed")
        raise
    except FreeSourceError as exc:
        await record_access(
            pool,
            "fmp",
            endpoint,
            AccessResult(AccessState.TEMPORARILY_FAILED, "FMP request failed"),
            status_code=exc.status_code,
        )
        raise
    await provider_gate.note_success(pool, "fmp", endpoint)
    await record_access(
        pool,
        "fmp",
        endpoint,
        AccessResult(AccessState.AVAILABLE, "free endpoint returned a response"),
        credits=1,
    )
    return body


async def collect_fmp_company(
    pool: asyncpg.Pool,
    http: httpx.AsyncClient,
    *,
    ticker: str,
    symbol: str,
    api_key: str,
    provider_settings: Any,
    include_estimates: bool = True,
) -> dict[str, int]:
    """Profile (sector/industry...) into `company_research_observation` and annual estimates
    into `forward_observation` (kind `published_analyst_consensus`). Two credits at most."""
    governor = PostgresRequestGovernor(pool)
    budget = endpoint_budget(provider_settings)
    now = dt.datetime.now(dt.UTC)
    counts = {"profile_fields": 0, "estimates": 0, "estimates_without_count": 0}

    body = await _fetch_governed(
        pool,
        http,
        governor,
        endpoint="profile",
        url=PROFILE_URL,
        params={"symbol": symbol},
        api_key=api_key,
        budget=budget,
    )
    profile = parse_profile(body, symbol) if body is not None else None
    source = f"{PROFILE_URL}?symbol={symbol}"
    if profile is None:
        await update_coverage(
            pool,
            CoverageUpdate(
                "fmp",
                "profile",
                ticker,
                AccessResult(AccessState.NOT_COVERED, "no FMP profile on the free plan"),
            ),
        )
    else:
        async with pool.acquire() as conn, conn.transaction():
            for name, text in profile.fields.items():
                await store_research_observation(
                    conn,
                    ticker=ticker,
                    provider="fmp",
                    dataset="profile",
                    metric=name,
                    text_value=text,
                    source_url=source,
                    match_confidence=Decimal("0.9"),  # symbol equality only, no issuer check
                    raw={"symbol": profile.symbol},
                )
        counts["profile_fields"] = len(profile.fields)
        await update_coverage(
            pool,
            CoverageUpdate(
                "fmp", "profile", ticker, AccessResult(AccessState.AVAILABLE, "profile stored")
            ),
        )
    if not include_estimates:
        return counts

    body = await _fetch_governed(
        pool,
        http,
        governor,
        endpoint="analyst_estimates",
        url=ESTIMATES_URL,
        params={"symbol": symbol, "period": "annual", "page": 0, "limit": ESTIMATE_YEARS},
        api_key=api_key,
        budget=budget,
    )
    estimates = parse_estimates(body, symbol) if body is not None else []
    source = f"{ESTIMATES_URL}?symbol={symbol}&period=annual"
    for item in estimates:
        if item.analyst_count is None:
            counts["estimates_without_count"] += 1
            continue
        await store_forward_value(
            pool,
            ForwardValue(
                ticker=ticker,
                kind="published_analyst_consensus",
                provider="fmp",
                metric=item.metric,
                basis="FMP annual analyst consensus (average)",
                fiscal_period=f"FY{item.fiscal_year}",
                period_end=item.period_end,
                value=item.value,
                low=item.low,
                high=item.high,
                analyst_count=item.analyst_count,
                known_at=now,
                source_url=source,
                raw=item.raw,
            ),
        )
        counts["estimates"] += 1
    await update_coverage(
        pool,
        CoverageUpdate(
            "fmp",
            "analyst_estimates",
            ticker,
            AccessResult(AccessState.AVAILABLE, f"{counts['estimates']} estimates stored")
            if counts["estimates"]
            else AccessResult(AccessState.NOT_COVERED, "no usable FMP estimates on the free plan"),
            available_periods=counts["estimates"],
        ),
    )
    return counts
