"""Macro read-side fixes: product-scoped trade context with partner rows and unmapped
remainder, first-release vs latest vintage history, and an unprobed provider reading as
access_unverified (never available) with exactly one access row per series."""

import datetime as dt
from collections.abc import AsyncIterator
from decimal import Decimal

import asyncpg
import httpx
import pytest

from steward.macro.providers import TradePoint
from steward.macro.read import context_for_holding
from steward.macro.store import store_trade_points

NOW = dt.datetime(2026, 10, 1, 9, tzinfo=dt.UTC)


def _utc(text: str) -> dt.datetime:
    return dt.datetime.fromisoformat(text).replace(tzinfo=dt.UTC)


@pytest.fixture
async def macro_api(backend_pool: asyncpg.Pool) -> AsyncIterator[httpx.AsyncClient]:
    from api.auth import AuthenticatedUser, require_owner
    from api.db import get_pool, get_pool_or_error
    from api.main import create_app

    app = create_app()

    async def pool_override() -> asyncpg.Pool:
        return backend_pool

    app.dependency_overrides[get_pool] = pool_override
    app.dependency_overrides[get_pool_or_error] = pool_override
    app.dependency_overrides[require_owner] = lambda: AuthenticatedUser(
        "7f0c3d4e-0000-4000-8000-000000000001", None, "aal2"
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


# ---------------------------------------------------------------------------------------
# Trade: product scope, partner rows, unmapped remainder, hhi trend
# ---------------------------------------------------------------------------------------


def _t(hs: str, flow: str, year: int, partner: str, value: int) -> TradePoint:
    from steward.macro.geo import iso2_for_m49

    return TradePoint(
        "842", partner, flow, "HS2022", hs, dt.date(year, 1, 1), Decimal(value), None, None,
        reporter_iso="US", partner_iso=iso2_for_m49(partner),
    )  # fmt: skip


async def _seed_trade(pool: asyncpg.Pool) -> None:
    points = [
        # Product 85, exports: World is the reporter total; DE and CN are the reported partners.
        _t("85", "export", 2024, "0", 300),
        _t("85", "export", 2024, "276", 150),
        _t("85", "export", 2024, "156", 150),
        _t("85", "export", 2025, "0", 400),
        _t("85", "export", 2025, "276", 200),
        _t("85", "export", 2025, "156", 100),  # 100 of the 400 has no partner row
        _t("85", "import", 2025, "0", 90),  # World only: no partner is known
        # Product 90 for the same reporter, with three periods and a very different level.
        _t("90", "export", 2023, "0", 100),
        _t("90", "export", 2024, "0", 100),
        _t("90", "export", 2025, "0", 5000),
        _t("90", "export", 2025, "251", 5000),
    ]
    await store_trade_points(pool, points, NOW, "https://example.test/comtrade")


@pytest.mark.db
async def test_trade_context_is_scoped_to_the_requested_product(
    backend_pool: asyncpg.Pool, macro_api: httpx.AsyncClient
) -> None:
    await _seed_trade(backend_pool)
    body = (await macro_api.get("/macro/trade/US/85")).json()
    assert body["periods"] == 2  # product 90's 2023 does not count
    assert Decimal(body["latest_value"]) == 400
    # Growth is the product's (400 / 300 - 1), not the reporter's (5400 / 400 - 1).
    assert Decimal(body["metrics"]["export_growth"]).quantize(Decimal("0.0001")) == Decimal(
        "0.3333"
    )
    assert body["metrics"]["product_mix_change"] is None  # one product has no mix
    # Export concentration is over product 85's partners only: (2/3)^2 + (1/3)^2.
    assert Decimal(body["metrics"]["export_partner_concentration"]).quantize(
        Decimal("0.0001")
    ) == Decimal("0.5556")
    wide = body["reporter_wide"]
    assert wide["periods"] == 3 and wide["products"] == 2
    assert Decimal(wide["metrics"]["export_growth"]) > 5  # (400 + 5000) / (300 + 100) - 1
    other = (await macro_api.get("/macro/trade/US/90")).json()
    assert other["periods"] == 3 and Decimal(other["latest_value"]) == 5000


@pytest.mark.db
async def test_product_known_at_and_source_come_from_its_own_rows(
    backend_pool: asyncpg.Pool, macro_api: httpx.AsyncClient
) -> None:
    await store_trade_points(
        backend_pool, [_t("85", "export", 2025, "0", 1)], NOW, "https://example.test/85"
    )
    await store_trade_points(
        backend_pool,
        [_t("90", "export", 2025, "0", 1)],
        NOW + dt.timedelta(days=5),
        "https://example.test/90",
    )
    body = (await macro_api.get("/macro/trade/US/85")).json()
    assert body["source_url"] == "https://example.test/85"
    assert dt.datetime.fromisoformat(body["known_at"]) == NOW


@pytest.mark.db
async def test_trade_partners_unmapped_remainder_and_hhi_trend(
    backend_pool: asyncpg.Pool, macro_api: httpx.AsyncClient
) -> None:
    await _seed_trade(backend_pool)
    body = (await macro_api.get("/macro/trade/US/85")).json()
    flows = {f["flow"]: f for f in body["partners"]}
    export = flows["export"]
    assert export["period"] == "2025-01-01"
    # World is the total, never a partner; the 100 without a partner row stays unmapped.
    assert [(p["partner"], p["partner_iso"], Decimal(p["value"])) for p in export["partners"]] == [
        ("276", "DE", Decimal(200)),
        ("156", "CN", Decimal(100)),
    ]
    assert [Decimal(p["share"]) for p in export["partners"]] == [Decimal("0.5"), Decimal("0.25")]
    summary = export["summary"]
    assert Decimal(summary["total"]) == 400 and summary["total_basis"] == "world_row"
    assert Decimal(summary["mapped_value"]) == 300
    assert Decimal(summary["unmapped_value"]) == 100
    assert Decimal(summary["unmapped_share"]) == Decimal("0.25")
    assert (
        sum(Decimal(p["share"]) for p in export["partners"]) + Decimal(summary["unmapped_share"])
        == 1
    )
    # HHI trend, oldest first: 2024 is 50/50 = 0.5, 2025 is (2/3)^2 + (1/3)^2.
    trend = [
        (h["period"], Decimal(h["hhi"]).quantize(Decimal("0.0001"))) for h in export["history"]
    ]
    assert trend == [("2024-01-01", Decimal("0.5000")), ("2025-01-01", Decimal("0.5556"))]
    # An import flow with only a World row: everything is unmapped, no partner, no hhi.
    imports = flows["import"]
    assert imports["partners"] == [] and imports["summary"]["hhi"] is None
    assert Decimal(imports["summary"]["unmapped_value"]) == 90
    assert Decimal(imports["summary"]["unmapped_share"]) == 1
    # history_periods bounds the trend.
    short = (await macro_api.get("/macro/trade/US/85?history_periods=1")).json()
    assert len(next(f for f in short["partners"] if f["flow"] == "export")["history"]) == 1


@pytest.mark.db
async def test_without_a_world_row_nothing_is_claimed_unmapped(
    backend_pool: asyncpg.Pool, macro_api: httpx.AsyncClient
) -> None:
    await store_trade_points(
        backend_pool,
        [_t("85", "export", 2025, "276", 30), _t("85", "export", 2025, "156", 70)],
        NOW,
        "https://example.test/x",
    )
    export = (await macro_api.get("/macro/trade/US/85")).json()["partners"][0]
    assert export["summary"]["total_basis"] == "partner_sum"
    assert export["summary"]["unmapped_value"] is None
    assert Decimal(export["partners"][0]["share"]) == Decimal("0.7")


# ---------------------------------------------------------------------------------------
# Macro: vintage history, as_of, access state
# ---------------------------------------------------------------------------------------


async def _issuer_holding(pool: asyncpg.Pool, ticker: str, geography: str) -> None:
    issuer = await pool.fetchval(
        "insert into issuer (legal_name, country) values ('Example', 'US') returning id"
    )
    await pool.execute(
        "insert into exposure_mapping (issuer_id, segment, geography, basis, weight, valid_from,"
        " created_at) values ($1, 's', $2, 'reported', 1, '2026-01-01', '2026-02-01')",
        issuer,
        geography,
    )
    await pool.execute(
        "insert into instrument (t212_ticker,name,currency,price_symbol) values ($1,$1,'USD',$1)",
        ticker,
    )
    security = await pool.fetchval(
        "insert into security (issuer_id,kind,name,currency) values ($1,'common','x','USD')"
        " returning id",
        issuer,
    )
    await pool.execute(
        "insert into instrument_security_link (ticker,security_id,status)"
        " values ($1,$2,'confirmed')",
        ticker,
        security,
    )


async def _obs(
    pool: asyncpg.Pool,
    period: str,
    value: str,
    revision: int,
    known: str,
    release: str | None,
    source: str = "oecd",
) -> None:
    await pool.execute(
        "insert into macro_observation (series_id,source,geography,period,value,unit,frequency,"
        " release_at,known_at,vintage,revision,source_url) values ('cpi',$7,'DE',$1,$2,'%',"
        "'monthly',$3,$4,$5,$6,'u')",
        dt.date.fromisoformat(period),
        Decimal(value),
        _utc(release) if release else None,
        _utc(known),
        f"v{revision}",
        revision,
        source,
    )


async def _seed_vintages(pool: asyncpg.Pool) -> None:
    await _issuer_holding(pool, "HOLD", "DE")
    # March: a backfill, release date unknown -> the first release was not archived.
    await _obs(pool, "2026-03-01", "90", 0, "2026-04-20", None)
    # April: first published 100, later revised to 110.
    await _obs(pool, "2026-04-01", "100", 0, "2026-05-15", "2026-05-15")
    await _obs(pool, "2026-04-01", "110", 1, "2026-06-15", "2026-06-15")
    # May: published once, never revised.
    await _obs(pool, "2026-05-01", "105", 0, "2026-06-15", "2026-06-15")


@pytest.mark.db
async def test_context_history_has_first_release_latest_and_unarchived_vintages(
    backend_pool: asyncpg.Pool,
) -> None:
    await _seed_vintages(backend_pool)
    as_of = _utc("2026-09-01")
    plain = await context_for_holding(backend_pool, "HOLD", as_of)
    assert plain.observations[0].history == []  # opt in only
    ctx = await context_for_holding(backend_pool, "HOLD", as_of, history=60)
    (point,) = ctx.observations
    assert [h.period for h in point.history] == [
        dt.date(2026, 3, 1),
        dt.date(2026, 4, 1),
        dt.date(2026, 5, 1),
    ]  # oldest first
    march, april, may = point.history
    assert march.value_latest == 90 and march.value_first_release is None
    assert march.vintage_unavailable and march.released_at is None
    assert (april.value_latest, april.value_first_release) == (Decimal(110), Decimal(100))
    assert not april.vintage_unavailable
    assert april.released_at == _utc("2026-05-15") and april.revised_at == _utc("2026-06-15")
    assert april.revision_count == 1
    assert (may.value_latest, may.value_first_release) == (Decimal(105), Decimal(105))
    assert may.revised_at is None and may.revision_count == 0
    # The bound keeps the newest periods.
    two = await context_for_holding(backend_pool, "HOLD", as_of, history=2)
    assert [h.period.month for h in two.observations[0].history] == [4, 5]


@pytest.mark.db
async def test_context_history_respects_as_of(backend_pool: asyncpg.Pool) -> None:
    await _seed_vintages(backend_pool)
    ctx = await context_for_holding(backend_pool, "HOLD", _utc("2026-06-01"), history=60)
    (point,) = ctx.observations
    # By 1 June the revision (15 June) and May's first release are not known yet.
    assert [h.period.month for h in point.history] == [3, 4]
    april = point.history[1]
    assert (april.value_latest, april.value_first_release) == (Decimal(100), Decimal(100))
    assert april.revised_at is None


@pytest.mark.db
async def test_context_history_route_parameter(
    backend_pool: asyncpg.Pool, macro_api: httpx.AsyncClient
) -> None:
    await _seed_vintages(backend_pool)
    url = "/macro/context/HOLD?as_of=2026-09-01T00:00:00Z"
    assert (await macro_api.get(url)).json()["observations"][0]["history"] == []
    body = (await macro_api.get(url + "&history=60")).json()
    assert len(body["observations"][0]["history"]) == 3
    assert (await macro_api.get(url + "&history=61")).status_code == 422


@pytest.mark.db
async def test_unprobed_provider_is_access_unverified_and_never_duplicates(
    backend_pool: asyncpg.Pool,
) -> None:
    await _seed_vintages(backend_pool)
    await backend_pool.execute("delete from provider_access where provider='oecd'")
    as_of = _utc("2026-09-01")
    (point,) = (await context_for_holding(backend_pool, "HOLD", as_of)).observations
    assert point.access_state == "access_unverified"  # no row: never probed, not available

    # Both endpoints present: still one observation, taken from the most recently probed row.
    await backend_pool.execute(
        "insert into provider_access (provider,endpoint,state,tested_at) values"
        " ('oecd','indicators','available','2026-09-01'),"
        " ('oecd','sdmx','not_covered','2026-09-20')"
    )
    points = (await context_for_holding(backend_pool, "HOLD", as_of)).observations
    assert [p.access_state for p in points] == ["not_covered"]
    await backend_pool.execute("update provider_access set tested_at=null where endpoint='sdmx'")
    points = (await context_for_holding(backend_pool, "HOLD", as_of)).observations
    assert [p.access_state for p in points] == ["available"]  # never-probed ranks last
