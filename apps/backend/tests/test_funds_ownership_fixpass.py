"""Funds / ownership backend fix-pass (audit F6-02, F6-05, F6-06, F6-10, F6-14, N-PORT flag).

Each test fails on the previous behaviour: see the docstring of each for what it reproduces.
"""

import datetime as dt
from collections.abc import AsyncIterator

import asyncpg
import httpx
import pytest

from api.auth import AuthenticatedUser, require_owner
from api.db import get_pool, get_pool_or_error
from api.main import create_app
from steward.config.jobs import LookThroughFund, LookThroughSettings
from steward.funds.aggregate import aggregate_exposure
from steward.lookthrough.coverage import (
    LedgerEntry,
    delete_manual_entry,
    read_ledger,
    refresh_ledger,
    save_entry,
)
from steward.lookthrough.read import PortfolioIn, PositionIn, RowIn, SnapshotIn
from tests.test_look_through import OWNER, TODAY, _seed_portfolio

CSPX_ISIN = "IE00B5BMR087"
ISIN_A = "US0000000001"
ISIN_B = "US0000000002"
SETTINGS = LookThroughSettings(
    funds=[
        LookThroughFund(isin=CSPX_ISIN, label="CSPX", provider="ishares", portfolio_id="253743")
    ],
    benchmark_proxies=[],
)


@pytest.fixture
async def api(backend_pool: asyncpg.Pool) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app()

    async def pool_override() -> asyncpg.Pool:
        return backend_pool

    app.dependency_overrides[get_pool] = pool_override
    app.dependency_overrides[get_pool_or_error] = pool_override
    app.dependency_overrides[require_owner] = lambda: AuthenticatedUser(OWNER, None, "aal2")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


# --------------------------------------------------------------------------- F6-02


def _row(name: str, isin: str, weight: float, sector: str, currency: str = "USD") -> RowIn:
    return RowIn(
        name=name,
        isin=isin,
        ticker=None,
        asset_class="equity",
        weight=weight,
        sector=sector,
        country="US",
        currency=currency,
        currency_derived=False,
    )


def test_issuer_split_across_sector_buckets_is_reported_per_bucket() -> None:
    """Issuer A is reported with two sector labels (direct config vs fund CSV, or ADR vs
    ordinary): per-bucket issuer weights add up to each bucket, not the issuer in one bucket."""
    portfolio = PortfolioIn(
        snapshot_date=dt.date(2026, 9, 28),
        base_currency="USD",
        account_currency="USD",
        cash_base=0.0,
        positions=[PositionIn("CSPX", "CSPX", CSPX_ISIN, "ETF", "US", "USD", 300.0)],
    )
    snap = SnapshotIn(
        isin=CSPX_ISIN,
        source="ishares_json",
        as_of=dt.date(2026, 9, 25),
        fetched_at=dt.datetime(2026, 9, 26, tzinfo=dt.UTC),
        fund_name=None,
        country_basis="country_of_risk",
        currency_basis="issuer",
        warnings=[],
        rows=[
            _row("A", ISIN_A, 0.2, "Information Technology"),
            _row("A", ISIN_A, 0.3, "Technology", currency="EUR"),
            _row("B", ISIN_B, 0.5, "Energy"),
        ],
    )
    report = aggregate_exposure(portfolio, SETTINGS, {CSPX_ISIN: snap}, as_of=TODAY)
    key = f"isin:{ISIN_A}"
    sector = next(v for v in report.exposures if v.dimension == "sector")
    assert sector.issuer_buckets[key] == pytest.approx(
        {"Information Technology": 0.2, "Technology": 0.3}
    )
    for view in report.exposures:  # additive: listed issuers sum to every classified bucket
        for bucket in view.buckets:
            listed = sum(b.get(bucket.key, 0.0) for b in view.issuer_buckets.values())
            assert listed == pytest.approx(bucket.weight), (view.dimension, bucket.key)
    currency = next(v for v in report.exposures if v.dimension == "currency")
    assert currency.issuer_buckets[key] == pytest.approx({"USD": 0.2, "EUR": 0.3})


# --------------------------------------------------------------------------- F6-10


async def _insert_snapshot(pool: asyncpg.Pool, isin: str, as_of: str) -> None:
    await pool.execute(
        "insert into public.etf_snapshot (fund_isin, provider, portfolio_id, source, as_of, raw,"
        " fetched_at) values ($1, 'ishares', '253743', 'ishares_json', $2, '{}'::jsonb, now())",
        isin,
        dt.date.fromisoformat(as_of),
    )


async def test_manual_ledger_row_is_removed_and_derived_row_restored(
    backend_pool: asyncpg.Pool,
) -> None:
    await _insert_snapshot(backend_pool, CSPX_ISIN, "2026-09-25")
    await refresh_ledger(backend_pool, SETTINGS, [], None)
    await save_entry(
        backend_pool,
        LedgerEntry(
            "fund", CSPX_ISIN, "CSPX", "official_holdings", "typed by owner", origin="manual"
        ),
    )
    manual = next(e for e in (await read_ledger(backend_pool)).entries)
    assert manual.origin == "manual" and manual.latest_holdings_as_of is None
    assert await delete_manual_entry(backend_pool, "fund", CSPX_ISIN) is True
    assert await delete_manual_entry(backend_pool, "fund", CSPX_ISIN) is False  # idempotent
    await refresh_ledger(backend_pool, SETTINGS, [], None)
    restored = next(e for e in (await read_ledger(backend_pool)).entries)
    assert restored.origin == "derived" and restored.tier == "issuer_downloadable"
    assert restored.latest_holdings_as_of == dt.date(2026, 9, 25)


async def test_api_delete_reverts_manual_row_and_is_idempotent(
    backend_pool: asyncpg.Pool, api: httpx.AsyncClient
) -> None:
    await _seed_portfolio(backend_pool)  # CSPXl_EQ is held without an issuer mapping
    first = (await api.post("/funds/coverage-ledger/refresh")).json()
    assert next(e for e in first["entries"] if e["subject_key"] == CSPX_ISIN)["origin"] == "derived"
    body = {
        "subject_kind": "fund",
        "subject_key": CSPX_ISIN,
        "label": "CSPX",
        "tier": "official_holdings",
        "reason": "owner verified",
    }
    put = (await api.put("/funds/coverage-ledger", json=body)).json()
    assert next(e for e in put["entries"] if e["subject_key"] == CSPX_ISIN)["origin"] == "manual"
    params = {"subject_kind": "fund", "subject_key": CSPX_ISIN}
    for _ in range(2):  # the second call is a no-op, not an error
        resp = await api.delete("/funds/coverage-ledger", params=params)
        assert resp.status_code == 200
        row = next(e for e in resp.json()["entries"] if e["subject_key"] == CSPX_ISIN)
        assert row["origin"] == "derived" and row["tier"] == "unavailable"
    assert (
        await api.delete("/funds/coverage-ledger", params={"subject_kind": "fund"})
    ).status_code == 422


# --------------------------------------------------------------------------- F6-14


async def _filing(pool: asyncpg.Pool, accession: str, period: str, filed: str, shares: int) -> None:
    await pool.execute(
        "insert into form13f_filing (accession, cik, manager_name, period, filed,"
        " submission_type, dataset_url) values ($1, '0000999999', 'M', $2, $3, '13F-HR', 'u')",
        accession,
        dt.date.fromisoformat(period),
        dt.date.fromisoformat(filed),
    )
    await pool.execute(
        "insert into institutional_holding (accession, infotable_sk, cusip, ticker, shares,"
        " shares_type) values ($1, 1, '037833100', 'AAPL_US_EQ', $2, 'SH')",
        accession,
        shares,
    )


async def test_ownership_rows_carry_previous_period_and_filing_lag(
    backend_pool: asyncpg.Pool, api: httpx.AsyncClient
) -> None:
    await backend_pool.execute(
        "insert into instrument (t212_ticker, isin) values ('AAPL_US_EQ', 'US0378331005')"
    )
    await backend_pool.execute("insert into form13f_dataset (url) values ('u')")
    await _filing(backend_pool, "0000000001-26-000001", "2025-09-30", "2025-11-10", 90)
    await _filing(backend_pool, "0000000001-26-000002", "2025-12-31", "2026-02-14", 100)
    body = (await api.get("/holdings/AAPL_US_EQ/ownership")).json()
    top = body["top_holders"][0]
    assert top["previous_period_end"] == "2025-09-30"
    assert top["filed"] == "2026-02-14" and top["filing_lag_days"] == 45
    latest, oldest = body["periods"]
    assert latest["previous_period"] == "2025-09-30" and oldest["previous_period"] is None
    assert latest["latest_filing_lag_days"] == 45 and latest["earliest_filing_lag_days"] == 45
    assert oldest["latest_filing_lag_days"] == 41


# --------------------------------------------------------------------------- F6-06


async def test_pdmr_and_insider_lookups_ignore_ticker_case(
    backend_pool: asyncpg.Pool, api: httpx.AsyncClient
) -> None:
    """The stored ticker keeps its lowercase T212 suffix ('RRl_EQ'); both spellings find it."""
    await backend_pool.execute(
        "insert into pdmr_notification (source_url, ticker, headline, announced_at, status)"
        " values ('https://rns.test/1', 'RRl_EQ', 'Director dealing', now(), 'parsed')"
    )
    acc = "0000000002-26-000001"
    await backend_pool.execute(
        "insert into insider_filing (accession, ticker, issuer_cik, form, filed, source_url)"
        " values ($1, 'RRl_EQ', '0000111111', '4', $2, 'u')",
        acc,
        dt.datetime.now(dt.UTC).date(),
    )
    await backend_pool.execute(
        "insert into insider_transaction (accession, row_index, reporting_owner_cik,"
        " transaction_code, shares, transaction_date) values ($1, 0, '0000222222', 'P', 10, $2)",
        acc,
        dt.datetime.now(dt.UTC).date(),
    )
    for spelling in ("RRl_EQ", "RRL_EQ", "rrl_eq"):
        pdmr = (await api.get(f"/holdings/{spelling}/pdmr-notifications")).json()
        assert len(pdmr["notifications"]) == 1, spelling
        activity = (await api.get(f"/holdings/{spelling}/insider-activity")).json()
        assert len(activity["transactions"]) == 1, spelling
        trades = (await api.get(f"/holdings/{spelling}/insider-trades")).json()
        assert len(trades) == 1, spelling


async def test_ticker_lookups_use_the_functional_indexes(backend_pool: asyncpg.Pool) -> None:
    names = {
        r["indexname"]
        for r in await backend_pool.fetch(
            "select indexname from pg_indexes where indexname in"
            " ('pdmr_notification_ticker_lower', 'insider_filing_ticker_lower',"
            " 'insider_filing_ticker_link_lower')"
        )
    }
    assert len(names) == 3


# --------------------------------------------------------------------------- F6-05


async def test_pdmr_status_is_also_reported_for_the_ticker_alone(
    backend_pool: asyncpg.Pool, api: httpx.AsyncClient
) -> None:
    """Other issuers' announcements fill the channel-wide counts; the holding with none of
    its own must not look covered."""
    rows = [("RRl_EQ", "parsed"), ("RRl_EQ", "no_template"), ("VODl_EQ", "parsed")]
    for i, (ticker, status) in enumerate(rows):
        await backend_pool.execute(
            "insert into pdmr_notification (source_url, ticker, headline, status)"
            " values ($1, $2, 'h', $3)",
            f"https://rns.test/{i}",
            ticker,
            status,
        )
    own = (await api.get("/holdings/RRl_EQ/pdmr-notifications")).json()
    assert own["status"]["attempted"] == 3  # channel-wide, unchanged
    assert own["ticker_status"] == {
        "state": "available",
        "detail": "1 of 2 PDMR announcement(s) parsed; not read: 1 no_template",
        "parsed": 1,
        "attempted": 2,
        "by_status": {"parsed": 1, "no_template": 1},
    }
    none = (await api.get("/holdings/AAPL_US_EQ/pdmr-notifications")).json()
    assert none["status"]["state"] == "available"
    assert none["ticker_status"]["state"] == "access_unverified"
    assert none["ticker_status"]["attempted"] == 0 and none["ticker_status"]["parsed"] == 0


# --------------------------------------------------------------------------- N-PORT


async def test_fund_positions_flag_truncation_and_total(
    backend_pool: asyncpg.Pool, api: httpx.AsyncClient
) -> None:
    await backend_pool.execute(
        "insert into instrument (t212_ticker, isin) values ('AAPL_US_EQ', 'US0378331005')"
    )
    for i in range(3):
        acc = f"0000000003-26-00000{i}"
        await backend_pool.execute(
            "insert into nport_filing (accession, form, registrant_cik, series_id,"
            " report_period_end, filed, reconcile_status, source_url) values"
            " ($1, 'NPORT-P', '0000333333', $2, '2026-06-30', '2026-08-20', 'confirmed', 'u')",
            acc,
            f"S00000000{i}",
        )
        await backend_pool.execute(
            "insert into nport_holding (accession, row_index, issuer_name, value_usd,"
            " held_ticker) values ($1, 0, 'Apple', $2, 'AAPL_US_EQ')",
            acc,
            100 + i,
        )
    cut = (await api.get("/holdings/AAPL_US_EQ/fund-positions", params={"limit": 2})).json()
    assert (
        len(cut["positions"]) == 2 and cut["matching_positions"] == 3 and cut["truncated"] is True
    )
    full = (await api.get("/holdings/AAPL_US_EQ/fund-positions")).json()
    assert (
        len(full["positions"]) == 3
        and full["matching_positions"] == 3
        and full["truncated"] is False
    )
