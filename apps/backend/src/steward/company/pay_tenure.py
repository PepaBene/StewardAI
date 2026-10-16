# ruff: noqa: E501
"""CEO pay and tenure (insight I13.4), for US SEC registrants.

- Pay: the chief executive's summary-compensation total for the latest fiscal year, from the
  proxy statement (DEF 14A) as Financial Modeling Prep gives it, with the salary share when the
  salary component is sent. The XBRL pay-versus-performance figures the proxy tags itself
  (`ecd:PeoTotalCompAmt`, `ecd:PeoActuallyPaidCompAmt`) are shown beside it, or alone when the
  provider row is missing.
- Tenure: the CEO's start date from the company graph when known; otherwise the first Form 4
  filed under a chief-executive title. Management and board averages use each current officer's
  or director's first Form 4 in that role. Form 4 history stored here starts at a date, so these
  are floors and say so.
- Ownership: the CEO's latest Form 4 holding (direct and indirect lines), as a share of shares
  outstanding.
Anything not filed with the SEC is `unavailable` with the reason.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

import asyncpg
from pydantic import BaseModel, Field

from steward.analytics.versions import version

CEO_TITLE = re.compile(r"\b(ceo|chief\s+executive|principal\s+executive)\b", re.IGNORECASE)
# A Form 4 filer counts as current when they filed within this window: directors receive grants
# every year and officers trade or receive awards at least once a year in practice.
CURRENT_WINDOW = dt.timedelta(days=730)
DAYS_PER_YEAR = Decimal("365.25")


def is_ceo(title: str | None) -> bool:
    return bool(title and CEO_TITLE.search(title))


def years_between(start: dt.date, end: dt.date) -> float:
    return round(float(Decimal((end - start).days) / DAYS_PER_YEAR), 1)


def average(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 1) if values else None


@dataclass(frozen=True, slots=True)
class Insider:
    cik: str
    name: str
    title: str | None
    first_officer: dt.date | None
    first_director: dt.date | None
    first_ceo: dt.date | None
    last_filed: dt.date
    officer_now: bool
    director_now: bool


def tenure_sets(
    insiders: list[Insider], today: dt.date
) -> tuple[list[tuple[str, float]], list[tuple[str, float]]]:
    """(management, board): (name, years) for the officers and directors who filed within the
    current window, measured from their first Form 4 in that role."""
    current = [i for i in insiders if today - i.last_filed <= CURRENT_WINDOW]
    management = [
        (i.name, years_between(i.first_officer, today))
        for i in current
        if i.officer_now and i.first_officer
    ]
    board = [
        (i.name, years_between(i.first_director, today))
        for i in current
        if i.director_now and i.first_director
    ]
    return management, board


class PayFigure(BaseModel):
    label: str
    fiscal_year: int | None
    value: float
    currency: str
    source: str
    source_url: str | None


class TenurePerson(BaseModel):
    name: str
    years: float


class PayAndTenure(BaseModel):
    ticker: str
    status: Literal["complete", "partial", "unavailable"]
    reason: str | None
    ceo_name: str | None
    ceo_title: str | None
    pay: list[PayFigure] = Field(
        description="The CEO's pay for the latest fiscal year, one row per source."
    )
    salary_share: float | None = Field(
        description="Salary over total pay, fraction; None when the salary component was not sent."
    )
    pay_fiscal_year: int | None
    ceo_tenure_years: float | None
    ceo_since: dt.date | None
    ceo_tenure_basis: str | None
    ceo_shares: float | None
    ceo_share_of_outstanding: float | None
    ceo_ownership_as_of: dt.date | None
    management_avg_tenure: float | None
    management: list[TenurePerson]
    board_avg_tenure: float | None
    board: list[TenurePerson]
    form4_since: dt.date | None = Field(
        description="Earliest stored Form 4 for this company; tenures from Form 4 are floors from here."
    )
    caveats: list[str]
    method_version: str


def _unavailable(ticker: str, reason: str) -> PayAndTenure:
    return PayAndTenure(
        ticker=ticker, status="unavailable", reason=reason, ceo_name=None, ceo_title=None, pay=[],
        salary_share=None, pay_fiscal_year=None, ceo_tenure_years=None, ceo_since=None,
        ceo_tenure_basis=None, ceo_shares=None, ceo_share_of_outstanding=None, ceo_ownership_as_of=None,
        management_avg_tenure=None, management=[], board_avg_tenure=None, board=[], form4_since=None,
        caveats=[], method_version=version("pay_and_tenure"),
    )  # fmt: skip


_INSIDER_SCOPE = """(f.ticker = $1 or exists (select 1 from public.insider_filing_ticker l
                     where l.accession = f.accession and l.ticker = $1))"""


async def _insiders(pool: asyncpg.Pool, ticker: str) -> tuple[list[Insider], dt.date | None]:
    rows = await pool.fetch(
        f"""select cik, max(owner_name) as name,
                   (array_agg(officer_title order by filed desc) filter (where officer_title is not null))[1] as title,
                   min(filed) filter (where is_officer) as first_officer,
                   min(filed) filter (where is_director) as first_director,
                   min(filed) filter (where officer_title ~* '(\\mceo\\M|chief\\s+executive|principal\\s+executive)') as first_ceo,
                   max(filed) as last_filed,
                   coalesce(bool_or(is_officer) filter (where filed = last_filed), false) as officer_now,
                   coalesce(bool_or(is_director) filter (where filed = last_filed), false) as director_now
            from (select t.reporting_owner_cik as cik, t.owner_name, t.officer_title, t.is_officer, t.is_director, f.filed,
                         max(f.filed) over (partition by t.reporting_owner_cik) as last_filed
                  from public.insider_filing f join public.insider_transaction t using (accession)
                  where {_INSIDER_SCOPE} and (t.is_officer or t.is_director)) x
            group by cik""",  # noqa: S608 - constant fragments only
        ticker,
    )
    scope_sql = f"select min(f.filed) from public.insider_filing f where {_INSIDER_SCOPE}"  # noqa: S608
    since = await pool.fetchval(scope_sql, ticker)
    out = [
        Insider(
            cik=r["cik"], name=r["name"] or r["cik"], title=r["title"], first_officer=r["first_officer"],
            first_director=r["first_director"], first_ceo=r["first_ceo"], last_filed=r["last_filed"],
            officer_now=bool(r["officer_now"]), director_now=bool(r["director_now"]),
        )
        for r in rows
    ]  # fmt: skip
    return out, since


async def _ceo_holding(
    pool: asyncpg.Pool, ticker: str, cik: str
) -> tuple[Decimal | None, dt.date | None]:
    row = await pool.fetchrow(
        f"""with latest as (
              select distinct on (t.direct_indirect) t.shares_owned_after, t.transaction_date
              from public.insider_filing f join public.insider_transaction t using (accession)
              where {_INSIDER_SCOPE} and t.reporting_owner_cik = $2 and not t.derivative
                and t.shares_owned_after is not null and f.superseded_by_accession is null
              order by t.direct_indirect, t.transaction_date desc nulls last, f.filed desc)
            select sum(shares_owned_after) as shares, max(transaction_date) as as_of from latest""",  # noqa: S608
        ticker,
        cik,
    )
    return (row["shares"], row["as_of"]) if row else (None, None)


async def pay_and_tenure(
    pool: asyncpg.Pool, ticker: str, today: dt.date | None = None
) -> PayAndTenure:
    today = today or dt.date.today()
    inst = await pool.fetchrow(
        "select isin, cik from public.instrument where t212_ticker = $1", ticker
    )
    if inst is None:
        return _unavailable(ticker, "Not a known instrument.")
    if inst["cik"] is None:
        country = (inst["isin"] or "")[:2]
        return _unavailable(
            ticker,
            f"Not an SEC registrant{f' ({country})' if country else ''}: CEO pay comes from the US proxy statement "
            "(DEF 14A) and tenure from Form 4, which only SEC-registered issuers file. Home-market annual "
            "reports are not parsed for pay.",
        )
    caveats: list[str] = []
    comp_rows = await pool.fetch(
        """select distinct on (text_value, metric) text_value as person, value, currency, source_url, raw
           from public.company_research_observation
           where ticker=$1 and dataset='executive_compensation' and value is not null and text_value is not null
           order by text_value, metric, known_at desc""",
        ticker,
    )
    ceo_comp: list[tuple[int, str, Decimal, str, str | None, dict[str, str]]] = []
    for r in comp_rows:
        raw = json.loads(r["raw"]) if isinstance(r["raw"], str) else (r["raw"] or {})
        year = raw.get("fiscal_year")
        if isinstance(year, int) and is_ceo(r["person"]):
            ceo_comp.append(
                (
                    year,
                    r["person"],
                    r["value"],
                    r["currency"] or "USD",
                    r["source_url"],
                    raw.get("components") or {},
                )
            )
    ceo_comp.sort(key=lambda c: (-c[0], -c[2]))
    ecd = await pool.fetch(
        """select distinct on (concept) concept, value, currency, period_end, source_url
           from public.financial_statement_fact
           where ticker=$1 and concept = any($2::text[]) and value is not null and coalesce(dimensions->'members', '[]'::jsonb) = '[]'::jsonb
           order by concept, period_end desc, known_at desc""",
        ticker,
        ["ecd:PeoTotalCompAmt", "ecd:PeoActuallyPaidCompAmt"],
    )
    pay: list[PayFigure] = []
    salary_share: float | None = None
    pay_year: int | None = None
    ceo_name: str | None = None
    if ceo_comp:
        year, person, total, currency, url, components = ceo_comp[0]
        pay_year, ceo_name = year, person
        pay.append(
            PayFigure(
                label="Total compensation (summary table)",
                fiscal_year=year,
                value=float(total),
                currency=currency,
                source="DEF 14A via Financial Modeling Prep",
                source_url=url,
            )
        )
        salary = components.get("salary")
        if salary is not None and total > 0:
            salary_share = float(Decimal(salary) / total)
        elif salary is None:
            caveats.append("The salary component was not sent, so the salary share is not shown.")
    for r in ecd:
        label = (
            "Total compensation (pay-versus-performance table)"
            if r["concept"] == "ecd:PeoTotalCompAmt"
            else "Compensation actually paid"
        )
        pay.append(
            PayFigure(
                label=label,
                fiscal_year=r["period_end"].year,
                value=float(r["value"]),
                currency=r["currency"] or "USD",
                source=f"DEF 14A XBRL ({r['concept']})",
                source_url=r["source_url"],
            )
        )
        pay_year = pay_year or r["period_end"].year
    if not pay:
        caveats.append("No proxy-statement pay figure is stored for the chief executive.")

    insiders, form4_since = await _insiders(pool, ticker)
    edge = await pool.fetchrow(
        """select object_name, valid_from from public.company_graph_edge
           where subject_key=$1 and edge_type='executive' and status='confirmed' and valid_to is null
           order by valid_from desc nulls last limit 1""",
        ticker,
    )
    current_ceos = sorted(
        (i for i in insiders if is_ceo(i.title) and today - i.last_filed <= CURRENT_WINDOW),
        key=lambda i: i.last_filed, reverse=True,
    )  # fmt: skip
    ceo = current_ceos[0] if current_ceos else None
    ceo_since: dt.date | None = None
    basis: str | None = None
    if edge is not None and edge["valid_from"] is not None:
        ceo_since, basis = edge["valid_from"], "start date from the company graph"
        ceo_name = ceo_name or edge["object_name"]
    elif ceo is not None and ceo.first_ceo is not None:
        ceo_since = ceo.first_ceo
        basis = "first Form 4 under a chief-executive title"
        if form4_since is not None and ceo.first_ceo <= form4_since + dt.timedelta(days=90):
            basis += f" (stored Form 4 history starts {form4_since:%Y-%m}, so a floor)"
    if ceo is not None:
        ceo_name = ceo_name or ceo.name
    ceo_shares: Decimal | None = None
    ceo_as_of: dt.date | None = None
    if ceo is not None:
        ceo_shares, ceo_as_of = await _ceo_holding(pool, ticker, ceo.cik)
    else:
        caveats.append(
            "No current Form 4 filer holds a chief-executive title, so the CEO's own holding is not shown."
        )
    outstanding = await pool.fetchval(
        "select shares from public.shares_outstanding where ticker=$1 and method <> 'manual' order by as_of desc limit 1",
        ticker,
    )
    management, board = tenure_sets(insiders, today)
    if form4_since is not None:
        caveats.append(
            f"Management and board tenure count from each person's first Form 4 in the role; the stored history starts {form4_since:%Y-%m}, so long tenures are floors."
        )
    if not insiders:
        caveats.append(
            "No Form 4 filings are stored for this company, so tenure and ownership are not shown."
        )
    filled = [
        bool(pay),
        ceo_since is not None,
        ceo_shares is not None,
        bool(management),
        bool(board),
    ]
    status: Literal["complete", "partial", "unavailable"] = (
        "complete" if all(filled) else ("partial" if any(filled) else "unavailable")
    )
    return PayAndTenure(
        ticker=ticker, status=status,
        reason=None if status == "complete" else ("Nothing is stored yet for this company." if status == "unavailable" else "Some figures are missing; see caveats."),
        ceo_name=ceo_name, ceo_title=ceo.title if ceo else None, pay=pay, salary_share=salary_share,
        pay_fiscal_year=pay_year,
        ceo_tenure_years=years_between(ceo_since, today) if ceo_since else None, ceo_since=ceo_since,
        ceo_tenure_basis=basis,
        ceo_shares=None if ceo_shares is None else float(ceo_shares),
        ceo_share_of_outstanding=float(ceo_shares / outstanding) if ceo_shares is not None and outstanding else None,
        ceo_ownership_as_of=ceo_as_of,
        management_avg_tenure=average([y for _n, y in management]),
        management=[TenurePerson(name=n, years=y) for n, y in sorted(management, key=lambda p: -p[1])],
        board_avg_tenure=average([y for _n, y in board]),
        board=[TenurePerson(name=n, years=y) for n, y in sorted(board, key=lambda p: -p[1])],
        form4_since=form4_since, caveats=caveats, method_version=version("pay_and_tenure"),
    )  # fmt: skip
