"""Latest SEC N-PORT fund positions in a holding, with reporting lag and coverage state.

These are positions that registered funds report owning. They are NOT beneficial ownership of
the issuer, they lag the quarter end (public ~60 days after it), and they cover only the funds
the portfolio tracks. Nothing here is summed across funds or combined with Form 13F figures.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Literal

import asyncpg
from pydantic import BaseModel, Field

from steward.sources.nport import PUBLIC_LAG_DAYS, public_after

STALE_AFTER_DAYS = 150  # a quarterly report older than this has missed a filing cycle


class FundPosition(BaseModel):
    series_id: str
    fund_name: str | None
    registrant_name: str | None
    report_period_end: dt.date = Field(description="The date the positions are as of.")
    filed: dt.date
    public_after: dt.date = Field(
        description="Quarter end + 60 days: earliest the report is expected to be public."
    )
    lag_days: int = Field(description="Filing date minus report date, in days.")
    age_days: int = Field(description="As-of date minus report date, in days.")
    stale: bool
    issuer_name: str | None
    title: str | None
    cusip: str | None
    isin: str | None
    balance: Decimal | None
    units: str | None = Field(description="NS shares, PA principal amount, NC contracts, ...")
    currency: str | None
    value_usd: Decimal | None
    pct_of_fund: Decimal | None = Field(description="Percent of the fund's net assets as filed.")
    payoff_profile: str | None
    asset_cat: str | None
    issuer_cat: str | None
    is_derivative: bool
    derivative_cat: str | None
    map_basis: str | None = Field(description="Identifier that tied the row to this holding.")
    accession: str
    form: str
    source_url: str


class NportCoverage(BaseModel):
    state: Literal["available", "stale", "no_positions", "no_funds_loaded"]
    funds_loaded: int = Field(description="Tracked funds with a reconciled N-PORT filing.")
    funds_holding: int
    latest_report_period_end: dt.date | None
    oldest_report_period_end: dt.date | None
    scope: str = (
        "Only the portfolio's US-registered funds that lack an issuer holdings snapshot; "
        "absence of a fund is not evidence it does not hold the security."
    )


class HoldingFundPositions(BaseModel):
    ticker: str
    basis: Literal["fund_position"] = "fund_position"
    notice: str = (
        "Fund positions from SEC N-PORT: what each fund reports holding at the report date, "
        f"public about {PUBLIC_LAG_DAYS} days after quarter end. Not beneficial ownership; "
        "do not add to Form 13F holders, 13D/G stakes or issuer-published fund holdings."
    )
    as_of: dt.date
    positions: list[FundPosition]
    coverage: NportCoverage


LATEST = """
with latest as (
  select distinct on (series_id) *
  from public.nport_filing
  where reconcile_status = 'confirmed' and filed <= $1
  order by series_id, report_period_end desc, filed desc, accession desc
)
"""


async def holding_fund_positions(
    pool: asyncpg.Pool, ticker: str, *, as_of: dt.date | None = None, limit: int = 50
) -> HoldingFundPositions:
    as_of = as_of or dt.datetime.now(dt.UTC).date()
    funds = await pool.fetch(
        LATEST  # noqa: S608 - constant SQL, values bound as parameters
        + "select report_period_end from latest",
        as_of,
    )
    # Same security (share class) only: another class of the issuer is a different position.
    securities = await pool.fetchval(
        """select coalesce(array_agg(security_id) filter (where security_id is not null), '{}')
           from public.instrument_security_link where ticker=$1 and status='confirmed'""",
        ticker,
    )
    rows = await pool.fetch(
        LATEST  # noqa: S608 - constant SQL, values bound as parameters
        + """select f.series_id, f.series_name, f.registrant_name, f.report_period_end, f.filed,
                    f.accession, f.form, f.source_url, h.*
             from latest f join public.nport_holding h using (accession)
             where h.held_ticker = $2 or h.security_id = any($3::bigint[])
             order by h.value_usd desc nulls last, f.series_id, h.row_index limit $4""",
        as_of,
        ticker,
        securities,
        limit,
    )
    positions = [
        FundPosition(
            series_id=r["series_id"],
            fund_name=r["series_name"],
            registrant_name=r["registrant_name"],
            report_period_end=r["report_period_end"],
            filed=r["filed"],
            public_after=public_after(r["report_period_end"]),
            lag_days=(r["filed"] - r["report_period_end"]).days,
            age_days=(as_of - r["report_period_end"]).days,
            stale=(as_of - r["report_period_end"]).days > STALE_AFTER_DAYS,
            issuer_name=r["issuer_name"],
            title=r["title"],
            cusip=r["cusip"],
            isin=r["isin"],
            balance=r["balance"],
            units=r["units"],
            currency=r["currency"],
            value_usd=r["value_usd"],
            pct_of_fund=r["pct_value"],
            payoff_profile=r["payoff_profile"],
            asset_cat=r["asset_cat"],
            issuer_cat=r["issuer_cat"],
            is_derivative=r["is_derivative"],
            derivative_cat=r["derivative_cat"],
            map_basis=r["map_basis"],
            accession=r["accession"],
            form=r["form"],
            source_url=r["source_url"],
        )
        for r in rows
    ]
    periods = [r["report_period_end"] for r in funds]
    latest = max(periods, default=None)
    state: Literal["available", "stale", "no_positions", "no_funds_loaded"]
    if not periods:
        state = "no_funds_loaded"
    elif not positions:
        state = "no_positions"
    elif latest is not None and (as_of - latest).days > STALE_AFTER_DAYS:
        state = "stale"
    else:
        state = "available"
    return HoldingFundPositions(
        ticker=ticker,
        as_of=as_of,
        positions=positions,
        coverage=NportCoverage(
            state=state,
            funds_loaded=len(periods),
            funds_holding=len({p.series_id for p in positions}),
            latest_report_period_end=latest,
            oldest_report_period_end=min(periods, default=None),
        ),
    )
