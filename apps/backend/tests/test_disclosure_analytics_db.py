"""P8 analytics against a real database: coverage counts only what was known by the date, an
uncovered holding is counted as uncovered, and the screen excludes a holding with no verified value
instead of scoring it. Skipped without STEWARD_TEST_DATABASE_URL."""

import datetime as dt
from decimal import Decimal as D

import asyncpg
import pytest

from steward.disclosure import coverage_report, screening
from tests.conftest import MigratedDatabase

pytestmark = pytest.mark.db

UTC = dt.UTC
DAY = dt.date(2026, 6, 30)
AFTER = dt.datetime(2026, 7, 5, 12, tzinfo=UTC)
BEFORE = dt.datetime(2026, 6, 1, 12, tzinfo=UTC)


async def seed(pool: asyncpg.Pool) -> None:
    job_id = await pool.fetchval(
        "insert into public.job (job_type, trigger, status) values"
        " ('portfolio_sync','schedule','succeeded') returning id"
    )
    await pool.execute(
        "insert into public.account_snapshot (snapshot_date, job_id, captured_at, t212_account_id,"
        " account_currency, total_value, cash_available, cash_in_pies, cash_reserved,"
        " invested_value, invested_cost, unrealized_ppl, realized_ppl, base_currency, fx_source,"
        " fx_rate, cash_base, invested_base, total_base, weight_basis)"
        " values ($1,$2,now(),1,'CZK',0,0,0,0,0,0,0,0,'CZK','cnb',1,0,0,0,'total')",
        DAY,
        job_id,
    )
    for ticker, value, isin, cik in (
        ("AAA", 700, "US0000000002", "0000011111"),
        ("BBB", 300, "GB0000000001", None),
    ):
        await pool.execute(
            "insert into public.instrument (t212_ticker, isin, name, currency, security_type,"
            " home_country, cik) values ($1, $2, $1, 'USD', 'Common Stock', 'US', $3)",
            ticker,
            isin,
            cik,
        )
        await pool.execute(
            "insert into public.position_snapshot (snapshot_date, ticker, job_id, captured_at,"
            " quantity, avg_price, current_price, instrument_currency, value, account_currency,"
            " value_account, cost_account, ppl, base_currency, fx_source, fx_rate, value_base)"
            " values ($1,$2,$3,now(),1,$4,$4,'USD',$4,'CZK',$4,$4,0,'CZK','cnb',1,$4)",
            DAY,
            ticker,
            job_id,
            D(value),
        )
    await pool.execute(
        "insert into public.issuer (legal_name, cik, country) values ('Acme', '0000011111', 'US')"
    )
    await pool.execute(
        """insert into public.insider_filing (accession, ticker, issuer_cik, form, filed,
                                              source_url, known_at)
           values ('0000011111-26-000001', 'AAA', '0000011111', '4', '2026-06-20',
                   'https://x', $1)""",
        dt.datetime(2026, 6, 21, tzinfo=UTC),
    )


async def test_a_13f_counts_toward_ownership_only_once_its_filing_day_has_ended(
    backend_pool: asyncpg.Pool,
) -> None:
    await seed(backend_pool)
    await backend_pool.execute(
        """insert into public.form13f_filing (accession, cik, manager_name, period, filed,
                                              submission_type)
           values ('0000033333-26-000001','0000033333','Fund LP','2026-03-31','2026-07-01',
                   '13F-HR')"""
    )
    await backend_pool.execute(
        """insert into public.institutional_holding (accession, infotable_sk, cusip, ticker,
                                                     value_usd, shares)
           values ('0000033333-26-000001', 1, '037833100', 'AAA', 1000, 10)"""
    )

    def owner_cell(out: dict) -> dict:
        return next(
            c for c in out["cells"] if c["dataset"] == "ownership" and c["jurisdiction"] == "US"
        )

    same_day = await coverage_report.coverage_view(
        backend_pool, dt.datetime(2026, 7, 1, 12, tzinfo=UTC)
    )
    assert owner_cell(same_day)["covered"] == 0
    next_day = await coverage_report.coverage_view(
        backend_pool, dt.datetime(2026, 7, 2, 1, tzinfo=UTC)
    )
    assert owner_cell(next_day)["covered"] == 1


async def test_watchlist_tickers_count_as_uncovered_holdings_but_add_no_value(
    backend_pool: asyncpg.Pool, migrated_db: MigratedDatabase
) -> None:
    await seed(backend_pool)
    admin = await asyncpg.connect(migrated_db.admin_url)
    try:
        user = await admin.fetchval(
            "insert into auth.users(id, email) values (gen_random_uuid(), 'o@example.com')"
            " returning id"
        )
    finally:
        await admin.close()
    await backend_pool.execute(
        "insert into public.watchlist_item(owner_id, ticker) values ($1, 'ZZZ')", user
    )
    plain = await coverage_report.coverage_view(backend_pool, AFTER)
    assert plain["holdings"] == 2 and plain["watchlist_only"] == 0
    wide = await coverage_report.coverage_view(backend_pool, AFTER, include_watchlist=True)
    assert wide["holdings"] == 3 and wide["watchlist_only"] == 1
    assert wide["denominator_value"] == plain["denominator_value"] == "1000"
    assert any("watchlist-only" in n for n in wide["notes"])


async def test_coverage_counts_known_filings_and_never_zero_fills_the_rest(
    backend_pool: asyncpg.Pool,
) -> None:
    await seed(backend_pool)
    out = await coverage_report.coverage_view(backend_pool, AFTER)
    assert out["holdings"] == 2 and out["denominator_value"] == "1000"
    cell = next(c for c in out["cells"] if c["dataset"] == "insiders" and c["jurisdiction"] == "US")
    assert (cell["covered"], cell["uncovered"], cell["covered_value"]) == (1, 0, "700")
    gb = next(c for c in out["cells"] if c["dataset"] == "insiders" and c["jurisdiction"] == "GB")
    assert (gb["covered"], gb["uncovered"], gb["covered_share"]) == (0, 1, str(D(0)))
    assert gb["first_known_at"] is None
    assert out["cost_ledger"]["total_usd"] == "0.00" and out["cost_ledger"]["paid_sources"] == 0
    assert all(q["error_rate"] is None for q in out["quality"])  # nothing adjudicated yet
    # a filing known only after the cutoff is not counted as covered
    early = await coverage_report.company_evidence_view(backend_pool, "AAA", BEFORE)
    ins = next(d for d in early["datasets"] if d["dataset"] == "insiders")
    assert (
        ins["count"] == 0
        and ins["latest_known_at"] is None
        and ins["coverage_state"] != "available"
    )
    late = await coverage_report.company_evidence_view(backend_pool, "AAA", AFTER)
    assert next(d for d in late["datasets"] if d["dataset"] == "insiders")["count"] == 1


async def test_screen_excludes_holdings_without_a_verified_value(
    backend_pool: asyncpg.Pool,
) -> None:
    await seed(backend_pool)
    out = await screening.screen_view(backend_pool, {"min_discretionary_buys": 1}, AFTER)
    assert out["universe_size"] == 2 and out["matched"] == []
    kinds = {e["ticker"]: e["kind"] for e in out["excluded"]}
    assert kinds == {
        "AAA": "failed",
        "BBB": "missing",
    }  # AAA has filings but no buys; BBB none at all
