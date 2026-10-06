"""The gap registry: which slots of a holding the structured sources left empty, and which of
those may be researched on the web.

A slot is a thing the holding page shows. Each declares how to tell it is filled by a
structured source, what kind of value it holds, which source tiers may fill it, and whether
the web can fill it at all. A slot that needs a paid licence is `unavailable`: recorded with
the reason so it is never searched for.
"""

import datetime as dt
from dataclasses import dataclass
from typing import Literal

import asyncpg


@dataclass(frozen=True)
class Slot:
    key: str
    kind: Literal["date", "number", "text", "segments"]
    ask: str  # what to find, for the search prompt
    t2_alone: bool = False  # a tier-2 source may confirm it without a second source
    allow_t3: bool = False  # reference sites (Wikipedia) may fill it
    unavailable: str | None = None  # why the web cannot fill it
    money: bool = False  # an amount: needs a currency code, and the quote may scale it


SLOTS: dict[str, Slot] = {
    "next_report_date": Slot(
        "next_report_date",
        "date",
        "the date of the company's next scheduled earnings / results announcement, as "
        "printed on its financial calendar or in a company or exchange announcement "
        "(an ISO date, YYYY-MM-DD)",
        t2_alone=True,
    ),
    "shares_outstanding": Slot(
        "shares_outstanding",
        "number",
        "the company's total number of shares outstanding (all share classes summed only if "
        "the page states the total), as a plain integer count, with the date it applies to",
    ),
    "company_description": Slot(
        "company_description",
        "text",
        "one or two plain sentences, copied verbatim from the page, saying what the company does",
        t2_alone=True,
        allow_t3=True,
    ),
    "annual_revenue": Slot(
        "annual_revenue",
        "number",
        "the company's total revenue (net sales) for its latest completed fiscal year, as stated "
        "in its annual results or annual report. Give the value as a plain integer in whole "
        "currency units (1.2 billion is 1200000000), the ISO currency code as the unit, and "
        "the fiscal year as the period",
        money=True,
    ),
    "segment_revenue": Slot(
        "segment_revenue",
        "segments",
        "the company's revenue by reportable segment (or product line) for its latest completed "
        "fiscal year, from its annual results or annual report. Give the value as a JSON list of "
        '{"segment": name, "amount": whole currency units as an integer}, between 2 and 8 '
        "entries, the ISO currency code as the unit and the fiscal year as the period. The quote "
        "must be one passage (up to 600 characters) that prints every segment name and amount",
        money=True,
    ),
    "analyst_estimates": Slot(
        "analyst_estimates",
        "number",
        "analyst consensus estimates",
        unavailable="consensus estimates are a paid vendor product (FMP, Refinitiv, FactSet)",
    ),
}


async def is_filled(pool: asyncpg.Pool, ticker: str, slot: str, today: dt.date) -> bool:
    """Does a structured source already fill the slot? Web facts never count."""
    if slot == "next_report_date":
        return bool(
            await pool.fetchval(
                "select next_report_date >= $2 and coalesce(report_date_source, '') not like"
                " 'web:%' from public.instrument where t212_ticker = $1",
                ticker,
                today,
            )
        )
    if slot == "shares_outstanding":
        return bool(
            await pool.fetchval(
                "select exists (select 1 from public.shares_outstanding where ticker = $1)",
                ticker,
            )
        )
    if slot == "segment_revenue":
        return bool(
            await pool.fetchval(
                """select exists (select 1 from public.segment_revenue sr
                                  join public.xbrl_filing f on f.id = sr.filing_id
                                  where f.ticker = $1 and sr.kind in ('segment', 'product')
                                    and sr.period_end - sr.period_start >= 350)""",
                ticker,
            )
        )
    if slot == "annual_revenue":
        return bool(
            await pool.fetchval(
                """select exists (select 1 from public.segment_revenue sr
                                  join public.xbrl_filing f on f.id = sr.filing_id
                                  where f.ticker = $1 and sr.kind = 'total'
                                    and sr.period_end - sr.period_start >= 350)
                       or exists (select 1 from public.financial_statement_fact
                                  where ticker = $1 and statement = 'income'
                                    and concept ilike '%revenue%')""",
                ticker,
            )
        )
    return False  # no structured source for a description: only web facts fill it


async def has_open_fact(pool: asyncpg.Pool, ticker: str, slot: str, today: dt.date) -> bool:
    """An approved fact, or one waiting for review, already exists (a date only while it is
    still ahead): nothing to search for."""
    return bool(
        await pool.fetchval(
            "select exists (select 1 from public.web_fact where ticker = $1 and slot = $2"
            " and status in ('approved', 'needs_review')"
            " and (value_date is null or value_date >= $3))",
            ticker,
            slot,
            today,
        )
    )


async def find_gaps(
    pool: asyncpg.Pool, tickers: list[str], slots: list[str], today: dt.date
) -> tuple[list[tuple[str, str]], list[tuple[str, str, str]]]:
    """((ticker, slot) to research, (ticker, slot, reason) that cannot be filled from the web).
    A slot is a gap while no structured source fills it and no web fact is open for it."""
    open_gaps: list[tuple[str, str]] = []
    unavailable: list[tuple[str, str, str]] = []
    for ticker in tickers:
        for key in slots:
            slot = SLOTS[key]
            if await is_filled(pool, ticker, key, today):
                continue
            if slot.unavailable:
                unavailable.append((ticker, key, slot.unavailable))
            elif not await has_open_fact(pool, ticker, key, today):
                open_gaps.append((ticker, key))
    return open_gaps, unavailable
