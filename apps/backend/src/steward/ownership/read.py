"""Ownership figures from stored 13F filings, per holding and report period.

Aggregation rules (the same an independent reader of the data set applies, see the tests):

- per manager (CIK) and period, the effective filing is the latest by filing date of the
  original 13F-HR and its RESTATEMENT amendments; NEW HOLDINGS amendments filed after it
  add their positions to it (a restatement is the whole report, so it replaces any
  NEW HOLDINGS amendment filed before it);
- holders: managers with a share position (type SH, not a put or call) in the CUSIP;
- shares and value: the sum of those positions (value in US dollars).

Every figure carries the report period and the latest filing date behind it; 13F data are
filed up to 45 days after the quarter and cover only managers above USD 100 M, so ownership
is shown with its date and never alerts.
"""

import datetime as dt
from decimal import Decimal

import asyncpg
from pydantic import BaseModel, Field

from steward.fundamentals.read import SharesOutstandingOut, latest_shares

EFFECTIVE = """
with f as (
  select * from public.form13f_filing where period = $1
), base as (
  select distinct on (cik) accession, cik, filed from f
  where submission_type = '13F-HR' or coalesce(amendment_type, '') <> 'NEW HOLDINGS'
  order by cik, filed desc, accession desc
), adds as (
  select a.accession, a.cik from f a left join base b on b.cik = a.cik
  where a.submission_type = '13F-HR/A' and a.amendment_type = 'NEW HOLDINGS'
    and (b.accession is null or (a.filed, a.accession) > (b.filed, b.accession))
), effective as (
  select accession, cik from base union select accession, cik from adds
)
"""


class TopHolder(BaseModel):
    cik: str
    manager_name: str | None
    shares: Decimal
    value_usd: Decimal | None
    accession: str = Field(description="The filing the position is read from.")
    filed: dt.date | None = Field(
        default=None,
        description="Latest filing date among this manager's effective filings of the period "
        "(an original 13F-HR or amendments).",
    )
    filing_lag_days: int | None = Field(
        default=None, description="`filed` minus the report period end, in days."
    )
    previous_period_end: dt.date | None = Field(
        default=None,
        description="The report period `prev_shares` / `shares_change_pct` are measured "
        "against (the previous period loaded, not necessarily the adjacent quarter); null "
        "when no earlier period is loaded.",
    )
    prev_shares: Decimal | None = Field(
        default=None,
        description="The same manager's shares in the previous report period loaded "
        "(`top_holders_previous_period`); null when it held none then or no earlier period "
        "is loaded.",
    )
    shares_change_pct: float | None = Field(
        default=None,
        description="(shares - prev_shares) / prev_shares, as a fraction (0.125 = +12.5 %); "
        "null when prev_shares is null or zero.",
    )
    pct_of_shares_outstanding: float | None = Field(
        default=None,
        description="shares x ads_ratio / shares outstanding, as a fraction; null without a "
        "shares-outstanding figure.",
    )


class OwnershipPeriod(BaseModel):
    period: dt.date
    holders: int
    shares: Decimal
    value_usd: Decimal
    latest_filed: dt.date | None
    earliest_filed: dt.date | None = Field(
        default=None, description="Earliest filing date of any manager's effective filing."
    )
    latest_filing_lag_days: int | None = Field(
        default=None,
        description="`latest_filed` minus `period`, in days: the latest filing of the period "
        "(including late amendments), not a single filing.",
    )
    earliest_filing_lag_days: int | None = Field(
        default=None, description="`earliest_filed` minus `period`, in days."
    )
    previous_period: dt.date | None = Field(
        default=None,
        description="The period the `*_change` figures are measured against (previous period "
        "loaded, possibly not the adjacent quarter); null for the oldest period loaded.",
    )
    holders_change: int | None = Field(description="Against the previous period loaded.")
    shares_change: Decimal | None
    new_holders: int | None
    exited_holders: int | None
    pct_of_shares_outstanding: float | None = Field(
        default=None,
        description="All 13F holders' shares (x ads_ratio) / shares outstanding, as a fraction.",
    )


class HoldingOwnership(BaseModel):
    ticker: str
    cusips: list[str]
    periods: list[OwnershipPeriod] = Field(description="Newest first.")
    top_holders: list[TopHolder] = Field(description="Largest positions, latest period.")
    top_holders_previous_period: dt.date | None = Field(
        default=None, description="The period each top holder's prev_shares is from."
    )
    source: str = "SEC Form 13F data sets"
    applicable: bool = Field(
        default=True, description="False for holdings Form 13F cannot cover (non-US listings)."
    )
    not_applicable_reason: str | None = None
    shares_outstanding: SharesOutstandingOut | None = Field(
        default=None, description="The denominator of the % figures, with its source."
    )
    pct_of_shares_outstanding: float | None = Field(
        default=None, description="Aggregate institutional %: the latest period's figure."
    )
    basis_label: str | None = Field(
        default=None,
        description="What the % measures, e.g. \"US 13F filers' holdings of the US line - a "
        'floor" for ADRs.',
    )


US_BASIS = "13F filers only (managers above USD 100 M); smaller holders are not counted"
ADR_BASIS = "US 13F filers' holdings of the US line — a floor"
ETF_TYPES = {"ETP", "ETF", "FUND"}


def pct_of(shares: Decimal | None, ratio: Decimal, outstanding: Decimal) -> float | None:
    if shares is None or outstanding <= 0:
        return None
    return float(shares * ratio / outstanding)


async def not_applicable_reason(pool: asyncpg.Pool, ticker: str) -> str | None:
    """Why Form 13F cannot cover a holding with no 13F positions loaded: a UCITS ETF, or a
    company listed only outside the US. None when 13F could cover it (not loaded yet)."""
    inst = await pool.fetchrow(
        "select isin, cik, security_type from public.instrument where t212_ticker = $1",
        ticker,
    )
    if inst is None:
        return None
    country = (inst["isin"] or "")[:2]
    if (inst["security_type"] or "").upper() in ETF_TYPES and country not in ("US", "CA"):
        return (
            f"Not applicable: an ETF domiciled in {country or 'Europe'}. Form 13F covers only "
            "Section 13(f) securities (US-listed shares), which this fund is not."
        )
    sec_filer = inst["cik"] is not None or await pool.fetchval(
        "select exists (select 1 from public.shares_outstanding where ticker = $1"
        " and method <> 'manual')",
        ticker,
    )
    if not sec_filer and country and country not in ("US", "CA"):
        return (
            f"Not applicable: listed only outside the US ({country}) and not an SEC registrant. "
            "Form 13F covers only Section 13(f) securities (US-listed shares)."
        )
    return None


async def _period_figures(
    pool: asyncpg.Pool, period: dt.date, cusips: list[str]
) -> tuple[int, Decimal, Decimal, dt.date | None, set[str], dt.date | None]:
    rows = await pool.fetch(
        EFFECTIVE + " select e.cik, sum(h.shares) as shares, sum(h.value_usd) as value,"  # noqa: S608 - constant SQL, values bound as parameters
        " max(f.filed) as filed, min(f.filed) as first_filed from effective e"
        " join public.institutional_holding h on h.accession = e.accession"
        " join public.form13f_filing f on f.accession = e.accession"
        " where h.cusip = any($2::text[]) and h.shares_type = 'SH' and h.put_call is null"
        " group by e.cik",
        period,
        cusips,
    )
    holders = {r["cik"] for r in rows}
    shares = sum((r["shares"] or Decimal(0) for r in rows), Decimal(0))
    value = sum((r["value"] or Decimal(0) for r in rows), Decimal(0))
    filed = max((r["filed"] for r in rows), default=None)
    first_filed = min((r["first_filed"] for r in rows), default=None)
    return len(holders), shares, value, filed, holders, first_filed


async def holding_ownership(
    pool: asyncpg.Pool, ticker: str, periods: int = 4, top: int = 10
) -> HoldingOwnership:
    cusips = [
        r["cusip"]
        for r in await pool.fetch(
            "select distinct cusip from public.institutional_holding where ticker = $1", ticker
        )
    ]
    found = [
        r["period"]
        for r in await pool.fetch(
            "select distinct f.period from public.form13f_filing f join"
            " public.institutional_holding h on h.accession = f.accession"
            " where h.cusip = any($1::text[]) order by f.period desc limit $2",
            cusips,
            periods + 1,
        )
    ]
    figures = [(p, *await _period_figures(pool, p, cusips)) for p in found]
    out: list[OwnershipPeriod] = []
    for i, (period, holders, shares, value, filed, ciks, first_filed) in enumerate(
        figures[:periods]
    ):
        prev = figures[i + 1] if i + 1 < len(figures) else None
        out.append(
            OwnershipPeriod(
                period=period,
                holders=holders,
                shares=shares,
                value_usd=value,
                latest_filed=filed,
                earliest_filed=first_filed,
                latest_filing_lag_days=(filed - period).days if filed else None,
                earliest_filing_lag_days=(first_filed - period).days if first_filed else None,
                previous_period=prev[0] if prev else None,
                holders_change=holders - prev[1] if prev else None,
                shares_change=shares - prev[2] if prev else None,
                new_holders=len(ciks - prev[5]) if prev else None,
                exited_holders=len(prev[5] - ciks) if prev else None,
            )
        )
    top_rows = []
    if found:
        top_rows = await pool.fetch(
            EFFECTIVE  # noqa: S608 - constant SQL, values bound as parameters
            + " select e.cik, max(f.manager_name) as manager_name, sum(h.shares) as shares,"
            " sum(h.value_usd) as value, max(e.accession) as accession,"
            " max(f.filed) as filed from effective e"
            " join public.institutional_holding h on h.accession = e.accession"
            " join public.form13f_filing f on f.accession = e.accession"
            " where h.cusip = any($2::text[]) and h.shares_type = 'SH' and h.put_call is null"
            " group by e.cik order by sum(h.shares) desc, e.cik limit $3",
            found[0],
            cusips,
            top,
        )
    previous = found[1] if len(found) > 1 else None
    prev_shares: dict[str, Decimal] = {}
    if previous is not None and top_rows:
        prev_shares = {
            r["cik"]: r["shares"]
            for r in await pool.fetch(
                EFFECTIVE  # noqa: S608 - constant SQL, values bound as parameters
                + " select e.cik, sum(h.shares) as shares from effective e"
                " join public.institutional_holding h on h.accession = e.accession"
                " where h.cusip = any($2::text[]) and h.shares_type = 'SH'"
                " and h.put_call is null and e.cik = any($3::text[]) group by e.cik",
                previous,
                cusips,
                [r["cik"] for r in top_rows],
            )
        }
    outstanding = await latest_shares(pool, ticker)
    counted = outstanding is not None and outstanding.method != "manual"
    ratio = outstanding.ads_ratio if outstanding else Decimal(1)
    denominator = outstanding.shares if outstanding else Decimal(0)
    if counted:
        for p in out:
            p.pct_of_shares_outstanding = pct_of(p.shares, ratio, denominator)
    reason = None if cusips else await not_applicable_reason(pool, ticker)
    basis_label = None
    if counted and out and outstanding is not None:
        basis_label = ADR_BASIS if outstanding.foreign_issuer or ratio != 1 else US_BASIS
    return HoldingOwnership(
        ticker=ticker,
        cusips=cusips,
        periods=out,
        applicable=reason is None,
        not_applicable_reason=reason,
        shares_outstanding=outstanding,
        pct_of_shares_outstanding=out[0].pct_of_shares_outstanding if out else None,
        basis_label=basis_label,
        top_holders=[
            TopHolder(
                cik=r["cik"],
                manager_name=r["manager_name"],
                shares=r["shares"],
                value_usd=r["value"],
                accession=r["accession"],
                filed=r["filed"],
                filing_lag_days=(r["filed"] - found[0]).days if r["filed"] else None,
                previous_period_end=previous,
                prev_shares=prev_shares.get(r["cik"]),
                shares_change_pct=share_change(r["shares"], prev_shares.get(r["cik"])),
                pct_of_shares_outstanding=(
                    pct_of(r["shares"], ratio, denominator) if counted else None
                ),
            )
            for r in top_rows
        ],
        top_holders_previous_period=previous,
    )


def share_change(shares: Decimal, prev: Decimal | None) -> float | None:
    """(shares - prev) / prev as a fraction; None without a positive previous position."""
    if prev is None or prev <= 0:
        return None
    return float((shares - prev) / prev)


class ManagerChange(BaseModel):
    cik: str
    manager_name: str | None
    ticker: str
    change: str = Field(description="new or exited")
    period: dt.date
    previous_period: dt.date
    shares: Decimal | None = Field(description="New: shares held; exited: shares held before.")
    accession: str


async def tracked_manager_changes(
    pool: asyncpg.Pool, ciks: list[str], since: dt.datetime | None = None
) -> list[ManagerChange]:
    """New and exited portfolio positions of tracked managers between their two latest
    periods (only periods whose filing arrived after `since`, when given)."""
    changes: list[ManagerChange] = []
    for raw in ciks:
        cik = raw.zfill(10)
        periods = [
            r["period"]
            for r in await pool.fetch(
                "select distinct period from public.form13f_filing where cik = $1"
                " order by period desc limit 2",
                cik,
            )
        ]
        if len(periods) < 2:
            continue
        latest, previous = periods
        if since is not None:
            filed = await pool.fetchval(
                "select max(filed) from public.form13f_filing where cik = $1 and period = $2",
                cik,
                latest,
            )
            if filed is None or dt.datetime.combine(filed, dt.time(), dt.UTC) < since:
                continue

        async def held(period: dt.date, cik: str = cik) -> dict[str, tuple[Decimal, str, str]]:
            rows = await pool.fetch(
                EFFECTIVE  # noqa: S608 - constant SQL, values bound as parameters
                + " select h.ticker, sum(h.shares) as shares, max(e.accession) as accession,"
                " max(f.manager_name) as name from effective e"
                " join public.institutional_holding h on h.accession = e.accession"
                " join public.form13f_filing f on f.accession = e.accession"
                " where e.cik = $2 and h.ticker is not null and h.shares_type = 'SH'"
                " and h.put_call is null group by h.ticker",
                period,
                cik,
            )
            return {r["ticker"]: (r["shares"], r["accession"], r["name"]) for r in rows}

        now, before = await held(latest), await held(previous)
        name = next((v[2] for v in (*now.values(), *before.values())), None)
        accession_now = await pool.fetchval(
            "select max(accession) from public.form13f_filing where cik = $1 and period = $2",
            cik,
            latest,
        )
        for ticker in sorted(set(now) - set(before)):
            changes.append(
                ManagerChange(
                    cik=cik,
                    manager_name=name,
                    ticker=ticker,
                    change="new",
                    period=latest,
                    previous_period=previous,
                    shares=now[ticker][0],
                    accession=now[ticker][1],
                )
            )
        for ticker in sorted(set(before) - set(now)):
            changes.append(
                ManagerChange(
                    cik=cik,
                    manager_name=name,
                    ticker=ticker,
                    change="exited",
                    period=latest,
                    previous_period=previous,
                    shares=before[ticker][0],
                    accession=accession_now or before[ticker][1],
                )
            )
    return changes
