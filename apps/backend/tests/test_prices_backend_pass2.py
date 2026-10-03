"""Backend pass 2 (prices): the portfolio correlation matrix, raw bars next to adjusted ones,
the quote unit of every holding's prices, and the corporate-action decision's evidence
validation. Bars are seeded (a GBX listing, a 2:1 split, a short series)."""

import datetime as dt
import math
from collections.abc import AsyncIterator

import asyncpg
import httpx
import pytest

from api.auth import AuthenticatedUser, require_owner
from api.db import get_pool
from api.main import create_app
from steward.config.holding_profiles import HoldingProfile, HoldingProfilesConfig
from steward.config.store import save_config
from steward.reference.read import load_series
from steward.reference.risk import correlation

from .test_prices_stream import OWNER, firds_and_figi, instrument, put_bars, snapshot

pytestmark = pytest.mark.db

START = dt.date(2026, 6, 1)


def days(n: int) -> list[dt.date]:
    out, d = [], START
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += dt.timedelta(days=1)
    return out


def closes(ds: list[dt.date], phase: float, trend: float = 0.0) -> dict[dt.date, str]:
    return {d: f"{100 * math.exp(0.02 * math.sin(i * 0.9 + phase) + trend * i):.4f}"
            for i, d in enumerate(ds)}  # fmt: skip


@pytest.fixture
async def api(backend_pool: asyncpg.Pool) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app()

    async def pool_override() -> asyncpg.Pool:
        return backend_pool

    app.dependency_overrides[get_pool] = pool_override
    app.dependency_overrides[require_owner] = lambda: AuthenticatedUser(OWNER, None, "aal2")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


async def seed(pool: asyncpg.Pool) -> list[dt.date]:
    """USD (Stooq), a London listing in pence (Stooq), a short Stooq series, and a split
    holding on EODHD (raw halves at the split, adjusted does not)."""
    await instrument(pool, "AAA_US_EQ", currency="USD", symbol="aaa.us", mic="XNAS", isin=None)
    await instrument(pool, "VOD_EQ", currency="GBX", symbol="vod.uk", mic="XLON", isin=None)
    await instrument(pool, "NEW_US_EQ", currency="USD", symbol="new.us", mic="XNAS", isin=None)
    await instrument(pool, "SPL_US_EQ", currency="USD", symbol=None, mic="XNAS", isin=None)
    profiles = HoldingProfilesConfig(
        profiles=[HoldingProfile(ticker="SPL_US_EQ", eodhd_symbol="SPL.US")]
    )
    async with pool.acquire() as conn:
        await save_config(conn, "holding_profiles", profiles, created_by=None)
    ds = days(60)
    await put_bars(pool, "aaa.us", "stooq", closes(ds, 0.0), currency="USD")
    await put_bars(pool, "vod.uk", "stooq", closes(ds, 0.0, 0.001), currency="GBX")
    await put_bars(pool, "new.us", "stooq", closes(ds[-5:], 1.0), currency="USD")
    adjusted = closes(ds, 2.5)
    await put_bars(pool, "SPL.US", "eodhd", adjusted, currency="USD")
    split_at = 40
    raw = {
        d: (f"{float(c) * 2:.4f}" if i < split_at else c)
        for i, (d, c) in enumerate(adjusted.items())
    }
    await put_bars(pool, "SPL.US", "eodhd", raw, currency="USD", bar_type="raw")
    await snapshot(
        pool,
        ds[-1],
        [("AAA_US_EQ", "USD"), ("VOD_EQ", "GBX"), ("NEW_US_EQ", "USD"), ("SPL_US_EQ", "USD")],
    )
    return ds


async def test_correlation_matrix_agrees_with_the_per_holding_figures(
    backend_pool: asyncpg.Pool, api: httpx.AsyncClient
) -> None:
    await seed(backend_pool)
    body = (await api.get("/portfolio/correlation")).json()
    assert body["tickers"] == ["AAA_US_EQ", "NEW_US_EQ", "SPL_US_EQ", "VOD_EQ"]
    m, c, notes = body["matrix"], body["counts"], body["notes"]
    idx = {t: i for i, t in enumerate(body["tickers"])}
    assert all(len(row) == 4 for row in m) and m[0][0] == pytest.approx(1.0)
    # Symmetric, and the numbers are the ones the holding page computes.
    for a in idx:
        per = (await api.get(f"/holdings/{a}/prices")).json()["correlations"]
        for entry in per:
            assert m[idx[a]][idx[entry["ticker"]]] == entry["correlation"]
            assert m[idx[a]][idx[entry["ticker"]]] == m[idx[entry["ticker"]]][idx[a]]
    a, v = idx["AAA_US_EQ"], idx["VOD_EQ"]
    expected = correlation(await load_series(backend_pool, "aaa.us"),
                           await load_series(backend_pool, "vod.uk"))  # fmt: skip
    assert m[a][v] == pytest.approx(expected) and c[a][v] == 59
    # Too few shared observations is null (never 0), with the count and a reason.
    n = idx["NEW_US_EQ"]
    assert m[a][n] is None and c[a][n] == 4 and "fewer than the 20" in notes[a][n]
    assert m[n][n] is None
    # A lower threshold admits the pair; a different source pair carries a caution.
    low = (await api.get("/portfolio/correlation", params={"min_shared": 3})).json()
    assert low["matrix"][a][n] is not None
    s = idx["SPL_US_EQ"]
    assert "EODHD" in notes[a][s] and "Stooq" in notes[a][s]
    assert body["end"] == (await seed_end(backend_pool)).isoformat()
    assert [h["source"] for h in body["holdings"]] == ["Stooq", "Stooq", "EODHD", "Stooq"]
    assert (await api.get("/portfolio/correlation", params={"days": 5})).status_code == 422


async def seed_end(pool: asyncpg.Pool) -> dt.date:
    return days(60)[-1]


async def test_correlation_before_any_snapshot(api: httpx.AsyncClient) -> None:
    body = (await api.get("/portfolio/correlation")).json()
    assert body["tickers"] == [] and body["matrix"] == [] and body["end"] is None


async def test_raw_bars_are_the_same_series_own_never_spliced(
    backend_pool: asyncpg.Pool, api: httpx.AsyncClient
) -> None:
    ds = await seed(backend_pool)
    split = (await api.get("/holdings/SPL_US_EQ/prices", params={"days": 400})).json()
    assert split["source"] == "EODHD" and split["raw_bars"]["available"] is True
    raw = split["raw_bars"]
    assert raw["source"] == "EODHD" and raw["symbol"] == "SPL.US" and raw["quote_unit"] == "USD"
    by_adj = {b["date"]: b["close"] for b in split["bars"]}
    by_raw = {b["date"]: b["close"] for b in raw["bars"]}
    assert len(by_raw) == 60
    before, after = ds[10].isoformat(), ds[50].isoformat()
    assert by_raw[before] == pytest.approx(2 * by_adj[before], rel=1e-4)  # pre-split raw is 2x
    assert by_raw[after] == pytest.approx(by_adj[after])
    # Stooq stores adjusted only: an explicit reason, no bars, nothing borrowed from elsewhere.
    stooq = (await api.get("/holdings/AAA_US_EQ/prices")).json()
    assert stooq["raw_bars"] == {
        "available": False, "source": "Stooq", "symbol": "aaa.us", "quote_unit": None,
        "currency": None, "reason": "Stooq stores adjusted closes only", "bars": [],
    }  # fmt: skip


async def test_quote_unit_of_every_holding(
    backend_pool: asyncpg.Pool, api: httpx.AsyncClient
) -> None:
    await seed(backend_pool)
    body = (await api.get("/portfolio/holdings/prices")).json()["holdings"]
    units = {h["ticker"]: (h["quote_currency"], h["quote_currency_basis"]) for h in body}
    assert units == {
        "AAA_US_EQ": ("USD", "series"),
        "VOD_EQ": ("GBX", "series"),
        "NEW_US_EQ": ("USD", "series"),
        "SPL_US_EQ": ("USD", "series"),
    }
    # Pence from Yahoo's `GBp` spelling is normalised; a holding without bars falls back to
    # the instrument's currency.
    await put_bars(backend_pool, "bar.uk", "stooq", {days(3)[-1]: "50"}, currency="GBp")
    await instrument(backend_pool, "BAR_EQ", currency="GBX", symbol="bar.uk", mic="XLON", isin=None)
    await instrument(backend_pool, "EMP_EQ", currency="EUR", symbol="emp.de", mic="XETR", isin=None)
    await snapshot(backend_pool, dt.date(2026, 9, 30), [("BAR_EQ", "GBX"), ("EMP_EQ", "EUR")])
    body = (await api.get("/portfolio/holdings/prices")).json()["holdings"]
    units = {h["ticker"]: (h["quote_currency"], h["quote_currency_basis"]) for h in body}
    assert units == {"BAR_EQ": ("GBX", "series"), "EMP_EQ": ("EUR", "instrument")}
    assert (await api.get("/holdings/BAR_EQ/prices")).json()["quote_currency"] == "GBX"


async def test_corporate_action_decision_validates_the_evidence_id(
    backend_pool: asyncpg.Pool, api: httpx.AsyncClient
) -> None:
    await instrument(backend_pool, "CEZ_EQ")
    await firds_and_figi(backend_pool, "CEZ_EQ", "CZ0005112300", "XPRA", "CZK", "CEZ", "CP")
    from .test_prices_stream import confirmed_listing

    await confirmed_listing(backend_pool, "CEZ_EQ")
    created = (
        await api.post(
            "/holdings/CEZ_EQ/corporate-actions",
            json={
                "action_type": "split", "symbol": "cez.cz", "ex_date": "2026-09-10",
                "ratio": "2", "source": "issuer", "source_url": "https://example.test/s",
            },
        )
    ).json()  # fmt: skip
    url = f"/holdings/CEZ_EQ/corporate-actions/{created['id']}/decision"
    reason = "the issuer announced the split"
    missing = await api.post(
        url, json={"decision": "rejected", "reason": reason, "evidence_id": 987654}
    )
    assert missing.status_code == 422 and "987654 does not exist" in missing.json()["detail"]
    for bad in ("1e3", "0x10", "abc", 0, -1, 2**70):
        r = await api.post(url, json={"decision": "rejected", "reason": reason, "evidence_id": bad})
        assert r.status_code == 422, bad
    status = await backend_pool.fetchval(
        "select status from public.corporate_action where id = $1", created["id"]
    )
    assert status == "suggested"
