"""Browser-pass items 6 and 12: listing history rows are distinguishable; issuer-name lookup."""

import json
from typing import Any

import asyncpg
import httpx
import pytest

from tests.test_macro_fixes import macro_api  # noqa: F401  (fixture)

pytestmark = pytest.mark.db

ISIN = "US0378331005"


def _raw(rid: str, version: int, valid_from: str, valid_to: str | None, trade: str) -> str:
    # The shape of a real FIRDS Solr document (scrubbed to the fields the reader uses).
    doc: dict[str, Any] = {
        "id": rid,
        "_root_": rid,
        "_version_": version,
        "isin": ISIN,
        "mic": "OCXE",
        "status": "UNCH",
        "publication_date": "2026-09-27T05:38:56.832Z",
        "valid_from_date": valid_from,
        "mrkt_trdng_start_date": trade,
    }
    if valid_to:
        doc["valid_to_date"] = valid_to
    return json.dumps(doc)


async def _snap(pool: asyncpg.Pool, raw: str, content_hash: str) -> None:
    await pool.execute(
        "insert into listing_lifecycle_snapshot (isin,mic,publication_date,status,issuer_lei,"
        " currency,source_url,content_hash,known_at,raw) values ($1,'OCXE','2026-09-27','UNCH',"
        " 'HWUPKR0MPOU8FGXBT394','USD','u',$2,'2026-09-28',$3::jsonb)",
        ISIN,
        content_hash,
        raw,
    )


async def test_listing_history_collapses_identical_rows_and_exposes_what_differs(
    backend_pool: asyncpg.Pool,
    macro_api: httpx.AsyncClient,  # noqa: F811
) -> None:
    a = _raw("1", 11, "2026-07-08T00:00:00Z", "2026-07-09T00:00:00Z", "2026-06-26T06:00:00Z")
    a_again = _raw("1b", 12, "2026-07-08T00:00:00Z", "2026-07-09T00:00:00Z", "2026-06-26T06:00:00Z")
    b = _raw("2", 21, "2026-07-10T00:00:00Z", "2026-09-09T00:00:00Z", "2026-07-06T06:00:00Z")
    await _snap(backend_pool, a, "h1")
    await _snap(backend_pool, a_again, "h2")  # same record, only register-internal ids differ
    await _snap(backend_pool, b, "h3")
    response = await macro_api.get(f"/reference/listings/{ISIN}/history")
    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == 2  # not three: the re-read of the first state is not a new row
    assert {r["mic"] for r in rows} == {"OCXE"}
    assert sorted((r["valid_from"], r["valid_to"]) for r in rows) == [
        ("2026-07-08", "2026-07-09"),
        ("2026-07-10", "2026-09-09"),
    ]
    assert all(r["first_trade_at"] and r["published_at"] for r in rows)
