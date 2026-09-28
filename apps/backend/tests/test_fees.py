"""Trading212 fill fees (walletImpact.taxes): parsed, stored per charge, backfilled once,
and summed per period in base currency by GET /portfolio/transactions.

Fixture payloads follow Trading212's documented order shape. Tax quantities come both
positive (Trading212's own example) and negative (as live responses have been reported);
the stored amount is abs() either way.
"""

import copy
import datetime as dt
import json
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any

import asyncpg
import httpx
import pytest

from api.auth import AuthenticatedUser, require_owner
from api.db import get_pool
from api.main import create_app
from steward.portfolio.fees import fee_basis, fee_category, fee_charges
from steward.portfolio.sync import json_exact, order_row, sync_transactions
from steward.trading212 import Trading212Client
from steward.trading212.models import FillTax, HistoricalOrder

OWNER = "00000000-0000-0000-0000-00000000000a"


def _fill(
    fill_id: int,
    *,
    side: str = "BUY",
    ticker: str = "NVDA_US_EQ",
    quantity: str = "2",
    price: str = "176.66",
    fx_rate: str | None = "23.456",
    net_value: str | None = None,
    taxes: list[dict[str, Any]] | None = None,
    filled_at: str = "2026-09-20T14:00:00Z",
) -> dict[str, Any]:
    q, p = Decimal(quantity), Decimal(price)
    principal = q * p * (Decimal(fx_rate) if fx_rate else Decimal(1))
    net = Decimal(net_value) if net_value else (-principal if side == "BUY" else principal)
    impact: dict[str, Any] = {
        "currency": "CZK",
        "netValue": float(round(net, 2)),
        "realisedProfitLoss": 0,
    }
    if fx_rate:
        impact["fxRate"] = float(fx_rate)
    if taxes is not None:
        impact["taxes"] = taxes
    return {
        "order": {
            "id": fill_id * 10,
            "ticker": ticker,
            "side": side,
            "type": "MARKET",
            "status": "FILLED",
            "currency": "CZK",
            "initiatedFrom": "WEB",
            "instrument": {"ticker": ticker, "currency": "USD" if fx_rate else "CZK"},
        },
        "fill": {
            "id": fill_id,
            "filledAt": filled_at,
            "quantity": float(q) if side == "BUY" else -float(q),
            "price": float(p),
            "type": "TRADE",
            "tradingMethod": "TOTV",
            "walletImpact": impact,
            # A field StewardAI does not model: must survive in `raw`.
            "someFutureField": {"nested": [1, 2.50]},
        },
    }


FX_NEG = {
    "name": "CURRENCY_CONVERSION_FEE",
    "quantity": -12.43,
    "currency": "CZK",
    "chargedAt": "2026-09-20T14:00:01Z",
}
STAMP_POS = {"name": "FRENCH_TRANSACTION_TAX", "quantity": 3.66, "currency": "CZK"}


# ---------------------------------------------------------------------------
# Pure
# ---------------------------------------------------------------------------


def test_categories_and_abs_amounts() -> None:
    assert fee_category("CURRENCY_CONVERSION_FEE") == "fx"
    for name in ("STAMP_DUTY", "FINRA_FEE", "PTM_LEVY", "SOMETHING_NEW"):
        assert fee_category(name) == "tax"
    charges = fee_charges(
        [FillTax.model_validate(FX_NEG), FillTax.model_validate(STAMP_POS)], "CZK"
    )
    assert [(c.seq, c.category, c.amount, c.quantity) for c in charges] == [
        (0, "fx", Decimal("12.43"), Decimal("-12.43")),
        (1, "tax", Decimal("3.66"), Decimal("3.66")),
    ]
    no_currency = fee_charges([FillTax(name="PTM_LEVY", quantity=Decimal("1"))], "CZK")
    assert no_currency[0].currency == "CZK"  # the walletImpact (account) currency
    assert fee_charges(None, "CZK") == []


def test_fee_basis_tells_excluded_from_included() -> None:
    # Trading212's documented example: 5 x 185.5 USD x 0.79 = 732.725; netValue 732.72
    # with 3.66 stamp duty listed separately -> netValue excludes the fee.
    common = {"quantity": Decimal(5), "price": Decimal("185.5"), "fx_rate": Decimal("0.79")}
    assert (
        fee_basis(side="BUY", net_value=Decimal("732.72"), fees=Decimal("3.66"), **common)
        == "excluded"
    )
    # The same buy with the fee inside netValue.
    assert (
        fee_basis(side="BUY", net_value=Decimal("-736.38"), fees=Decimal("3.66"), **common)
        == "included"
    )
    # A sale pays out principal - fees when included.
    assert (
        fee_basis(side="SELL", net_value=Decimal("729.06"), fees=Decimal("3.66"), **common)
        == "included"
    )
    # fxRate the other way round (account units per instrument unit inverted) still works.
    inverted = {**common, "fx_rate": Decimal(1) / Decimal("0.79")}
    assert (
        fee_basis(side="BUY", net_value=Decimal("732.72"), fees=Decimal("3.66"), **inverted)
        == "excluded"
    )
    # Too far from both readings: unknown. No fees: nothing to decide.
    assert (
        fee_basis(side="BUY", net_value=Decimal("760"), fees=Decimal("3.66"), **common) == "unknown"
    )
    assert fee_basis(side="BUY", net_value=Decimal("1"), fees=Decimal(0), **common) is None
    assert (
        fee_basis(
            side="BUY",
            quantity=None,
            price=None,
            fx_rate=None,
            net_value=None,
            fees=Decimal(1),
        )
        == "unknown"
    )


def test_json_exact_keeps_numbers_as_received() -> None:
    payload = json.loads(
        '{"a": 0.0397585000, "b": [1, -0.57], "c": null, "d": "x"}', parse_float=Decimal
    )
    text = json_exact(payload)
    assert text == '{"a":0.0397585000,"b":[1,-0.57],"c":null,"d":"x"}'
    assert json.loads(text) == {"a": 0.0397585, "b": [1, -0.57], "c": None, "d": "x"}


async def _no_sleep(_: float) -> None:
    return None


def _client(
    pages: list[list[dict[str, Any]]], requests: list[str] | None = None
) -> Trading212Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if requests is not None:
            requests.append(str(request.url))
        if request.url.path.endswith("/equity/history/orders"):
            cursor = request.url.params.get("cursor")
            page = int(cursor) if cursor else 0
            has_next = page + 1 < len(pages)
            return httpx.Response(
                200,
                json={
                    "items": pages[page] if pages else [],
                    "nextPagePath": f"/api/v0/equity/history/orders?limit=50&cursor={page + 1}"
                    if has_next
                    else None,
                },
            )
        return httpx.Response(200, json={"items": [], "nextPagePath": None})

    return Trading212Client(
        "k", "s", env="demo", transport=httpx.MockTransport(handler), sleep=_no_sleep
    )


async def test_the_client_keeps_the_original_item_and_order_row_reads_taxes() -> None:
    item = _fill(1, taxes=[FX_NEG, STAMP_POS])
    async with _client([[item]]) as t212:
        pages = [page async for page in t212.iter_order_history()]
    parsed: HistoricalOrder = pages[0][0]
    assert parsed.source_payload is not None
    assert parsed.source_payload["fill"]["someFutureField"] == {"nested": [1, Decimal("2.50")]}
    assert parsed.fill is not None and parsed.fill.trading_method == "TOTV"
    row = order_row(parsed)
    assert row is not None
    # The original JSON, not a re-dump of the model (which would drop unknown fields).
    assert row.raw["fill"]["someFutureField"] == {"nested": [1, Decimal("2.50")]}
    assert row.raw["order"]["initiatedFrom"] == "WEB"
    assert [(f.name, f.amount) for f in row.fees] == [
        ("CURRENCY_CONVERSION_FEE", Decimal("12.43")),
        ("FRENCH_TRANSACTION_TAX", Decimal("3.66")),
    ]
    assert row.fee_basis == "excluded"


# ---------------------------------------------------------------------------
# Stored by the sync, backfilled once, summed by the API
# ---------------------------------------------------------------------------


@pytest.mark.db
async def test_sync_stores_fees_and_the_original_payload(backend_pool: asyncpg.Pool) -> None:
    pages = [
        [
            _fill(3, taxes=[FX_NEG, STAMP_POS]),
            _fill(2, side="SELL", taxes=[{**FX_NEG, "quantity": 9.1}]),
        ],
        [_fill(1, ticker="CEZ_EQ", fx_rate=None, price="1100", taxes=[])],
    ]
    async with _client(pages) as t212:
        detail = await sync_transactions(backend_pool, t212, job_id=None)  # type: ignore[arg-type]
    assert detail["orders_new"] == 3
    fees = await backend_pool.fetch(
        "select transaction_id, seq, name, category, amount, quantity, currency, charged_at"
        " from transaction_fee order by transaction_id, seq"
    )
    assert [(f["transaction_id"], f["name"], f["amount"], f["quantity"]) for f in fees] == [
        ("fill:2", "CURRENCY_CONVERSION_FEE", Decimal("9.1"), Decimal("9.1")),
        ("fill:3", "CURRENCY_CONVERSION_FEE", Decimal("12.43"), Decimal("-12.43")),
        ("fill:3", "FRENCH_TRANSACTION_TAX", Decimal("3.66"), Decimal("3.66")),
    ]
    assert fees[1]["charged_at"] == dt.datetime(2026, 9, 20, 14, 0, 1, tzinfo=dt.UTC)
    row = await backend_pool.fetchrow(
        "select raw, fees_captured_at, fee_basis from transaction where id = 'fill:3'"
    )
    raw = json.loads(row["raw"])
    assert raw["fill"]["walletImpact"]["taxes"][0]["quantity"] == -12.43
    assert raw["fill"]["someFutureField"] == {"nested": [1, 2.50]}
    assert row["fees_captured_at"] is not None and row["fee_basis"] == "excluded"
    cez = await backend_pool.fetchrow(
        "select fees_captured_at, fee_basis from transaction where id = 'fill:1'"
    )
    assert cez["fees_captured_at"] is not None and cez["fee_basis"] is None


@pytest.mark.db
async def test_fills_stored_before_fee_capture_are_backfilled_once(
    backend_pool: asyncpg.Pool,
) -> None:
    # As the previous code left it: a re-dumped model as raw (no taxes), no fee columns,
    # and a completed backfill with a watermark.
    old = _fill(1, taxes=[FX_NEG])
    stripped = copy.deepcopy(old)
    del stripped["fill"]["walletImpact"]["taxes"]
    await backend_pool.execute(
        "insert into transaction (id, type, subtype, ticker, quantity, price, amount, currency,"
        " occurred_at, raw) values ('fill:1', 'order', 'BUY', 'NVDA_US_EQ', 2, 176.66,"
        " -8287.5, 'CZK', '2026-09-20T14:00:00Z', $1::jsonb)",
        json.dumps(stripped),
    )
    await backend_pool.execute(
        "insert into sync_state (key, value) values ('trading212_history:orders', $1::jsonb)",
        json.dumps({"backfill_complete": True, "watermark": "2026-09-20T14:00:00+00:00"}),
    )
    newer = _fill(2, taxes=[STAMP_POS], filled_at="2026-09-21T09:00:00Z")
    pages = [[newer], [old]]
    requests: list[str] = []
    async with _client(pages, requests) as t212:
        detail = await sync_transactions(backend_pool, t212, job_id=None)  # type: ignore[arg-type]
    order_pages = [r for r in requests if "/history/orders" in r]
    assert len(order_pages) == 2  # read in full, despite backfill_complete
    assert detail["orders_new"] == 1 and detail["orders_updated"] == 1
    assert detail["orders_reread"] is True
    fees = await backend_pool.fetch(
        "select transaction_id, amount from transaction_fee order by transaction_id"
    )
    assert [(f["transaction_id"], f["amount"]) for f in fees] == [
        ("fill:1", Decimal("12.43")),
        ("fill:2", Decimal("3.66")),
    ]
    state = json.loads(
        await backend_pool.fetchval(
            "select value from sync_state where key = 'trading212_history:orders'"
        )
    )
    assert state["version"] == 1 and state["backfill_complete"] is True

    # The next run is incremental again: stops at the first page with a known fill, and
    # rewrites nothing.
    requests.clear()
    async with _client(pages, requests) as t212:
        detail = await sync_transactions(backend_pool, t212, job_id=None)  # type: ignore[arg-type]
    assert len([r for r in requests if "/history/orders" in r]) == 1
    assert detail["orders_new"] == 0 and "orders_updated" not in detail


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


async def _snapshot(pool: asyncpg.Pool, base: str = "CZK") -> None:
    job_id = await pool.fetchval(
        "insert into job (job_type, trigger, status) values ('portfolio_sync', 'manual',"
        " 'succeeded') returning id"
    )
    await pool.execute(
        "insert into account_snapshot (snapshot_date, job_id, captured_at, t212_account_id,"
        " account_currency, total_value, cash_available, cash_in_pies, cash_reserved,"
        " invested_value, invested_cost, unrealized_ppl, realized_ppl, base_currency,"
        " fx_source, fx_rate, cash_base, invested_base, total_base, weight_basis) values"
        " ('2026-09-25', $1, now(), 1, 'CZK', 1000, 0, 0, 0, 1000, 1000, 0, 0, $2, 'cnb',"
        " 1, 0, 1000, 1000, 'total')",
        job_id,
        base,
    )


@pytest.mark.db
async def test_api_fee_totals_and_row_badges(
    api: httpx.AsyncClient, backend_pool: asyncpg.Pool
) -> None:
    await _snapshot(backend_pool)
    # Before any fee capture: honest status, no sums.
    await backend_pool.execute(
        "insert into transaction (id, type, subtype, amount, currency, occurred_at, raw)"
        " values ('fill:9', 'order', 'BUY', -100, 'CZK', '2026-09-01T10:00:00Z', '{}')"
    )
    body = (await api.get("/portfolio/transactions", params={"from": "2026-09-01"})).json()
    assert body["fees"]["status"] == "not_captured" and body["fees"]["total"] == 0
    assert body["items"][0]["fees_captured"] is False

    # A USD-denominated charge converts at that day's ČNB rate; an account FEE counts too.
    await backend_pool.execute(
        "insert into fx_rate (source, rate_date, currency, home_currency, rate,"
        " published_amount, published_rate) values ('cnb', '2026-09-18', 'USD', 'CZK', 23.5,"
        " 1, 23.5)"
    )
    pages = [
        [
            _fill(3, taxes=[FX_NEG, STAMP_POS]),
            _fill(
                2, side="SELL", taxes=[{"name": "FINRA_FEE", "quantity": -0.1, "currency": "USD"}]
            ),
        ]
    ]
    async with _client(pages) as t212:
        await sync_transactions(backend_pool, t212, job_id=None)  # type: ignore[arg-type]
    await backend_pool.execute(
        "insert into transaction (id, type, subtype, amount, currency, occurred_at, raw)"
        " values ('cash:fee-1', 'cash', 'FEE', -25, 'CZK', '2026-09-22T10:00:00Z', '{}')"
    )
    body = (
        await api.get("/portfolio/transactions", params={"from": "2026-09-01", "to": "2026-09-30"})
    ).json()
    fees = body["fees"]
    assert fees["currency"] == "CZK" and fees["fx_source"] == "cnb"
    assert fees["fx_conversion"] == pytest.approx(12.43)
    assert fees["taxes_levies"] == pytest.approx(3.66 + 0.1 * 23.5)
    assert fees["account_fees"] == pytest.approx(25)
    assert fees["total"] == pytest.approx(12.43 + 3.66 + 2.35 + 25)
    assert fees["included_in_buys"] == 0 and fees["unconverted"] == []
    assert (fees["fills"], fees["fills_captured"], fees["fills_with_fees"]) == (3, 2, 2)
    assert fees["status"] == "partial"
    assert {n["name"] for n in fees["by_name"]} == {
        "CURRENCY_CONVERSION_FEE",
        "FRENCH_TRANSACTION_TAX",
        "FINRA_FEE",
        "FEE",
    }
    row = next(i for i in body["items"] if i["id"] == "fill:3")
    assert [(f["name"], f["category"], f["amount"]) for f in row["fees"]] == [
        ("CURRENCY_CONVERSION_FEE", "fx", 12.43),
        ("FRENCH_TRANSACTION_TAX", "tax", 3.66),
    ]
    assert row["fees_captured"] is True and row["fee_basis"] == "excluded"
    cash = next(i for i in body["items"] if i["id"] == "cash:fee-1")
    assert cash["fees"] == [] and cash["fees_captured"] is None

    # Filters apply: only dividends -> no fills, no fill fees.
    only_div = (await api.get("/portfolio/transactions", params={"type": "dividend"})).json()
    assert only_div["fees"]["status"] == "no_fills" and only_div["fees"]["total"] == 0

    # A fee in a currency with no stored rate is reported, not guessed.
    await backend_pool.execute(
        "insert into transaction (id, type, subtype, amount, currency, occurred_at, raw)"
        " values ('cash:fee-2', 'cash', 'FEE', -3, 'GBP', '2026-09-22T11:00:00Z', '{}')"
    )
    body = (await api.get("/portfolio/transactions", params={"from": "2026-09-01"})).json()
    assert body["fees"]["unconverted"] == ["GBP"]
    assert body["fees"]["account_fees"] == pytest.approx(25)


@pytest.mark.db
async def test_fees_included_in_net_value_are_reported_for_the_cash_flow(
    api: httpx.AsyncClient, backend_pool: asyncpg.Pool
) -> None:
    await _snapshot(backend_pool)
    # netValue already contains the 12.43 fee: -(2 x 176.66 x 23.456 + 12.43).
    principal = Decimal(2) * Decimal("176.66") * Decimal("23.456")
    net = -(principal + Decimal("12.43")).quantize(Decimal("0.01"))
    included = _fill(5, taxes=[FX_NEG], net_value=str(net))
    async with _client([[included]]) as t212:
        await sync_transactions(backend_pool, t212, job_id=None)  # type: ignore[arg-type]
    fees = (await api.get("/portfolio/transactions")).json()["fees"]
    assert fees["included_in_buys"] == pytest.approx(12.43)
    assert fees["included_in_sales"] == 0
