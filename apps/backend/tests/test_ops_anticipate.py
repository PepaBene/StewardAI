"""Anticipation: a held company reporting soon becomes one notice, gone once it has reported."""

import datetime as dt

import asyncpg
import pytest

from steward.ops import anticipate, tasks

TODAY = dt.date(2026, 10, 13)


def item(report: dt.date, research: dt.date | None) -> anticipate.Upcoming:
    return anticipate.Upcoming("AAPL_US_EQ", "Apple", report, 0.0612, research)


def test_the_notice_says_when_it_reports_and_how_old_the_research_is() -> None:
    title, detail = anticipate.describe(
        item(TODAY + dt.timedelta(days=1), TODAY - dt.timedelta(days=10)), TODAY
    )
    assert title == "Apple reports tomorrow"
    assert "6.1% of the portfolio" in detail and "10 days ago." in detail
    _, stale = anticipate.describe(item(TODAY, TODAY - dt.timedelta(days=90)), TODAY)
    assert "worth refreshing first" in stale
    title3, none = anticipate.describe(item(TODAY + dt.timedelta(days=3), None), TODAY)
    assert title3 == "Apple reports in 3 days" and "no research report yet" in none


@pytest.mark.db
async def test_only_held_companies_reporting_soon_are_announced_and_then_cleared(
    backend_pool: asyncpg.Pool,
) -> None:
    for ticker, name, days in (("AAPL_US_EQ", "Apple", 2), ("MSFT_US_EQ", "Microsoft", 20)):
        await backend_pool.execute(
            "insert into public.instrument (t212_ticker, name, next_report_date)"
            " values ($1, $2, $3)",
            ticker,
            name,
            TODAY + dt.timedelta(days=days),
        )
    # no holding yet: nothing to announce
    assert (await anticipate.announce(backend_pool, TODAY))["announced"] == 0

    job = await backend_pool.fetchval(
        "insert into public.job (job_type, trigger, status) values ('sync', 'manual', 'succeeded')"
        " returning id"
    )
    for ticker in ("AAPL_US_EQ", "MSFT_US_EQ"):
        await backend_pool.execute(
            "insert into public.position_snapshot (snapshot_date, ticker, job_id, captured_at,"
            " quantity, avg_price, current_price, instrument_currency, value, account_currency,"
            " value_account, cost_account, ppl, base_currency, fx_source, fx_rate, value_base,"
            " weight) values ($1, $2, $3, now(), 1, 1, 1, 'USD', 1, 'USD', 1, 1, 0, 'USD',"
            " 'ecb', 1, 1, 0.05)",
            TODAY,
            ticker,
            job,
        )
    assert (await anticipate.announce(backend_pool, TODAY))["announced"] == 1
    notice = (await tasks.needs_owner(backend_pool))[0]
    assert notice.kind == anticipate.KIND and notice.subject == "AAPL_US_EQ"

    later = TODAY + dt.timedelta(days=5)  # the date has passed
    out = await anticipate.announce(backend_pool, later)
    assert out["announced"] == 0 and out["resolved"] == 1
