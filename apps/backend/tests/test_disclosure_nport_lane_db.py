"""P3 fund lane against a real database: a confirmed N-PORT filing appears in its own lane with
its lag, an unconfirmed one does not, and neither is added to the 13F lane."""

import datetime as dt
from decimal import Decimal as D

import asyncpg
import pytest

from steward.disclosure import ownership_read

pytestmark = pytest.mark.db

UTC = dt.UTC
PERIOD = dt.date(2025, 12, 31)
AFTER = dt.datetime(2026, 6, 1, tzinfo=UTC)
BEFORE_FILING = dt.datetime(2026, 2, 1, tzinfo=UTC)


async def nport(
    pool: asyncpg.Pool,
    accession: str,
    series: str,
    status: str = "confirmed",
    filed: dt.date = dt.date(2026, 2, 27),
    shares: str = "2500",
) -> None:
    await pool.execute(
        """insert into public.nport_filing
             (accession, form, registrant_cik, registrant_name, series_id, series_name,
              report_period_end, filed, reconcile_status, source_url)
           values ($1,'NPORT-P','0000099999','Index Trust',$2,'Index Fund',$3,$4,$5,'https://x')""",
        accession,
        series,
        PERIOD,
        filed,
        status,
    )
    await pool.execute(
        """insert into public.nport_holding
             (accession, row_index, issuer_name, balance, units, is_derivative, held_ticker)
           values ($1, 0, 'Apple Inc', $2, 'NS', false, 'AAPL')""",
        accession,
        D(shares),
    )


def lane(out: dict, key: str) -> dict:
    return next(x for x in out["lanes"] if x["key"] == key)


async def test_confirmed_fund_filing_is_its_own_lane_and_not_in_13f(
    backend_pool: asyncpg.Pool,
) -> None:
    pool = backend_pool
    await pool.execute(
        "insert into public.instrument (t212_ticker, currency) values ('AAPL','USD')"
        " on conflict do nothing"
    )
    await pool.execute(
        """insert into public.form13f_filing
             (accession, cik, manager_name, period, filed, submission_type)
           values ('0000012345-26-000001','0000012345','Alpha Capital',$1,'2026-02-10','13F-HR')""",
        PERIOD,
    )
    await pool.execute(
        """insert into public.institutional_holding
             (accession, infotable_sk, cusip, ticker, shares, value_usd)
           values ('0000012345-26-000001', 1, '037833100', 'AAPL', 1000, 1000)"""
    )
    await nport(pool, "0000099999-26-000001", "S000000001")
    await nport(pool, "0000099999-26-000002", "S000000002", status="unmatched")

    out = await ownership_read.ownership(pool, "AAPL", AFTER)
    fund = lane(out, "fund_nport")
    assert fund["status"] == "covered"
    assert [e["holder"] for e in fund["entries"]] == ["Index Fund"]  # the unmatched one is absent
    entry = fund["entries"][0]
    assert D(entry["shares"]) == D(2500) and entry["lag_days"] == 58  # 31 Dec to 27 Feb
    assert entry["known_at"] == dt.datetime(2026, 2, 27, 23, 59, 59, tzinfo=UTC)

    f13 = lane(out, "institutional_13f")
    assert D(f13["total_shares"]) == D(1000)  # the fund's 2500 is not added to the 13F lane
    assert [e["holder"] for e in f13["entries"]] == ["Alpha Capital"]
    assert D(fund["total_shares"]) == D(2500)
    assert "combined_total" not in out

    early = await ownership_read.ownership(pool, "AAPL", BEFORE_FILING)
    assert lane(early, "fund_nport")["status"] == "no_data"  # not known before it was filed
