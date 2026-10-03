"""Browser-pass item 12: the macro exposure editor can label issuers by name."""

import asyncpg
import httpx
import pytest

from tests.test_macro_fixes import _holding, macro_api  # noqa: F401  (fixture)

pytestmark = pytest.mark.db


async def _issuer(pool: asyncpg.Pool, name: str, **cols: str) -> int:
    return int(
        await pool.fetchval(
            "insert into issuer (legal_name, lei, cik, country) values ($1,$2,$3,$4) returning id",
            name,
            cols.get("lei"),
            cols.get("cik"),
            cols.get("country"),
        )
    )


async def test_issuers_are_listed_by_name_with_their_held_tickers(
    backend_pool: asyncpg.Pool,
    macro_api: httpx.AsyncClient,  # noqa: F811
) -> None:
    apple = await _issuer(backend_pool, "Apple Inc.", cik="0000320193", country="US")
    cez = await _issuer(backend_pool, "ČEZ, a. s.", country="CZ")
    await _issuer(backend_pool, "Unheld 100% Corp")
    await _holding(backend_pool, "AAPL_US_EQ", apple)
    everything = (await macro_api.get("/macro/issuers")).json()
    assert {i["name"] for i in everything} == {"Apple Inc.", "Unheld 100% Corp", "ČEZ, a. s."}
    by_name = {i["name"]: i for i in everything}
    assert by_name["Apple Inc."]["tickers"] == ["AAPL_US_EQ"]
    assert by_name["Apple Inc."]["cik"] == "0000320193" and by_name["ČEZ, a. s."]["tickers"] == []
    picked = (await macro_api.get("/macro/issuers", params=[("ids", apple), ("ids", cez)])).json()
    assert {i["id"] for i in picked} == {apple, cez}
    found = (await macro_api.get("/macro/issuers", params={"q": "100%"})).json()
    assert [i["name"] for i in found] == ["Unheld 100% Corp"]  # a literal, not a wildcard
    assert (await macro_api.get("/macro/issuers", params={"limit": 0})).status_code == 422


async def test_exposure_rows_carry_the_issuer_name(
    backend_pool: asyncpg.Pool,
    macro_api: httpx.AsyncClient,  # noqa: F811
) -> None:
    issuer = await _issuer(backend_pool, "Example Corp")
    created = await macro_api.post(
        "/macro/exposures",
        json={
            "issuer_id": issuer, "segment": "s", "basis": "reported", "weight": "0.5",
            "geography": "DE", "valid_from": "2026-01-01",
        },
    )  # fmt: skip
    assert created.status_code == 201 and created.json()["issuer_name"] == "Example Corp"
    listed = (await macro_api.get("/macro/exposures")).json()
    assert [(e["issuer_id"], e["issuer_name"]) for e in listed] == [(issuer, "Example Corp")]
