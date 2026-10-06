"""Writing built geography rows against a real database (needs STEWARD_TEST_DATABASE_URL)."""

import datetime as dt
from decimal import Decimal
from typing import Any

import asyncpg

from steward.exposures.build import GeoShare, write_geography

D = Decimal


def facts(end: dt.date) -> dict[str, Any]:
    return {
        "accession": "0000000000-26-000001",
        "form": "10-K",
        "instance_url": "https://www.sec.gov/x.xml",
        "period_start": dt.date(end.year - 1, end.month, end.day),
        "period_end": end,
    }


async def issuer(pool: asyncpg.Pool) -> int:
    value = await pool.fetchval(
        "insert into public.issuer (legal_name) values ('Exposure Test Inc') returning id"
    )
    assert isinstance(value, int)
    return value


async def test_refresh_closes_old_rows_and_respects_owner_deletion(
    backend_pool: asyncpg.Pool,
) -> None:
    issuer_id = await issuer(backend_pool)
    first = [
        GeoShare("US", D("0.6"), "United States", D(60)),
        GeoShare("JP", D("0.1"), "Japan", D(10)),
    ]
    counts = await write_geography(
        backend_pool, issuer_id, first, facts(dt.date(2025, 12, 31)), D(100)
    )
    assert counts["inserted"] == 2

    # the owner deletes the Japan row; the next period's build must not bring it back
    await backend_pool.execute(
        "delete from public.exposure_mapping where issuer_id = $1 and geography = 'JP'", issuer_id
    )
    second = [
        GeoShare("US", D("0.5"), "United States", D(55)),
        GeoShare("JP", D("0.2"), "Japan", D(22)),
    ]
    counts = await write_geography(
        backend_pool, issuer_id, second, facts(dt.date(2026, 12, 31)), D(110)
    )
    assert counts["inserted"] == 1 and counts["deleted_by_owner"] == 1
    rows = await backend_pool.fetch(
        "select geography, weight, valid_from, valid_to from public.exposure_mapping"
        " where issuer_id = $1 order by valid_from",
        issuer_id,
    )
    assert [(r["geography"], r["valid_to"] is None) for r in rows] == [("US", False), ("US", True)]


async def test_owner_entries_are_never_touched(backend_pool: asyncpg.Pool) -> None:
    issuer_id = await issuer(backend_pool)
    await backend_pool.execute(
        "insert into public.exposure_mapping (issuer_id, segment, geography, basis, weight,"
        " valid_from) values ($1, 'mine', 'DE', 'user_assigned', 1, '2025-01-01')",
        issuer_id,
    )
    shares = [GeoShare("US", D("0.6"), "United States", D(60))]
    counts = await write_geography(
        backend_pool, issuer_id, shares, facts(dt.date(2025, 12, 31)), D(100)
    )
    assert counts["inserted"] == 0 and counts["owner_entries"] == 1
