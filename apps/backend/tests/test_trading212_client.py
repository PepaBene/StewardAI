"""Trading212 client against mocked responses shaped like the documented API.

Phase 0 AC: zero write-capable calls anywhere in the client's code. Enforced three ways:
the static check below, the runtime non-GET guard, and read-only API key scopes.
"""

import ast
import base64
import inspect
import json
import time
from decimal import Decimal

import httpx
import pytest

from steward.trading212 import ReadOnlyViolation, Trading212Client, Trading212Error
from steward.trading212 import client as client_module

SUMMARY = {
    "cash": {"availableToTrade": 1000.50, "inPies": 250.75, "reservedForOrders": 150.00},
    "currency": "CZK",
    "id": 12345678,
    "investments": {
        "currentValue": 5000.00,
        "realizedProfitLoss": 500.00,
        "totalCost": 4500.00,
        "unrealizedProfitLoss": 500.00,
    },
    "totalValue": 6000.50,
}

POSITIONS = [
    {
        "averagePricePaid": 170.1,
        "createdAt": "2025-03-04T10:15:00Z",
        "currentPrice": 228.37,
        "instrument": {
            "currency": "USD",
            "isin": "US0378331005",
            "name": "Apple",
            "ticker": "AAPL_US_EQ",
        },
        "quantity": 3.5,
        "quantityAvailableForTrading": 3.5,
        "quantityInPies": 0,
        "walletImpact": {
            "currency": "CZK",
            "currentValue": 18123.45,
            "fxImpact": -210.2,
            "totalCost": 13400.0,
            "unrealizedProfitLoss": 4723.45,
        },
        "someFutureField": "ignored",
    }
]


async def _no_sleep(_: float) -> None:
    return None


def _client(handler: httpx.MockTransport, **kwargs: object) -> Trading212Client:
    return Trading212Client(
        "key", "secret", env="demo", transport=handler, sleep=_no_sleep, **kwargs
    )  # type: ignore[arg-type]


async def test_parses_account_summary_and_positions_with_exact_decimals() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        body = SUMMARY if request.url.path.endswith("/account/summary") else POSITIONS
        return httpx.Response(200, content=json.dumps(body))

    async with _client(httpx.MockTransport(handler)) as t212:
        summary = await t212.get_account_summary()
        positions = await t212.get_positions()

    assert summary.currency == "CZK"
    assert summary.cash.available_to_trade == Decimal("1000.5")
    assert positions[0].instrument.ticker == "AAPL_US_EQ"
    assert positions[0].wallet_impact.current_value == Decimal("18123.45")
    assert positions[0].quantity == Decimal("3.5")

    assert [r.url.path for r in seen] == [
        "/api/v0/equity/account/summary",
        "/api/v0/equity/positions",
    ]
    assert all(r.url.host == "demo.trading212.com" for r in seen)
    expected_auth = "Basic " + base64.b64encode(b"key:secret").decode()
    assert all(r.headers["Authorization"] == expected_auth for r in seen)


async def test_live_env_uses_live_host() -> None:
    hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host)
        return httpx.Response(200, json=[])

    async with Trading212Client("k", "s", env="live", transport=httpx.MockTransport(handler)) as t:
        await t.get_instruments()
    assert hosts == ["live.trading212.com"]


async def test_changed_response_shape_fails_loudly() -> None:
    broken = [{**POSITIONS[0], "walletImpact": None}]
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json=broken))
    async with _client(transport) as t212:
        with pytest.raises(Trading212Error, match="Unexpected Position list response shape"):
            await t212.get_positions()


async def test_retries_429_then_succeeds_and_respects_reset_header() -> None:
    calls = 0
    sleeps: list[float] = []

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            reset = str(int(time.time()) + 5)
            return httpx.Response(429, headers={"x-ratelimit-reset": reset})
        return httpx.Response(200, json=POSITIONS)

    async def record_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    t212 = Trading212Client("k", "s", transport=httpx.MockTransport(handler), sleep=record_sleep)
    positions = await t212.get_positions()
    await t212.aclose()
    assert len(positions) == 1 and calls == 2
    assert sleeps and sleeps[0] >= 2  # waited for the window, not a blind immediate retry


async def test_exhausted_window_delays_next_call_to_same_endpoint() -> None:
    sleeps: list[float] = []
    reset = str(int(time.time()) + 30)

    def handler(_: httpx.Request) -> httpx.Response:
        headers = {"x-ratelimit-remaining": "0", "x-ratelimit-reset": reset}
        return httpx.Response(200, json=SUMMARY, headers=headers)

    async def record_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    async with Trading212Client(
        "k", "s", transport=httpx.MockTransport(handler), sleep=record_sleep
    ) as t212:
        await t212.get_account_summary()
        assert sleeps == []
        await t212.get_account_summary()
    assert len(sleeps) == 1 and 25 < sleeps[0] <= 30


@pytest.mark.parametrize(("status", "hint"), [(401, "key and secret"), (403, "read scope")])
async def test_auth_errors_are_not_retried(status: int, hint: str) -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status)

    async with _client(httpx.MockTransport(handler)) as t212:
        with pytest.raises(Trading212Error, match=hint) as err:
            await t212.get_positions()
    assert err.value.status_code == status and calls == 1


async def test_gives_up_after_max_retries_on_server_errors() -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503)

    async with _client(httpx.MockTransport(handler), max_retries=2) as t212:
        with pytest.raises(Trading212Error, match="503 after 2 retries"):
            await t212.get_account_summary()
    assert calls == 3


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
async def test_non_get_requests_never_leave_the_process(method: str) -> None:
    sent: list[httpx.Request] = []
    transport = httpx.MockTransport(lambda r: sent.append(r) or httpx.Response(200))  # type: ignore[func-returns-value]
    t212 = _client(transport)
    with pytest.raises(ReadOnlyViolation):
        # Reaching into the private httpx client simulates a future edit adding a write call.
        await t212._http.request(method, "/equity/orders/market", json={"quantity": 1})
    await t212.aclose()
    assert sent == []


def test_client_source_contains_no_write_calls() -> None:
    tree = ast.parse(inspect.getsource(client_module))
    called = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    write_like = {"post", "put", "patch", "delete", "request", "send", "stream"}
    assert called & write_like == set(), f"write-capable calls in client: {called & write_like}"
    public = [name for name in vars(Trading212Client) if not name.startswith("_")]
    # Public API: single reads (get_*) and paginated reads (iter_*) only.
    assert all(
        name.startswith(("get_", "iter_")) or name in {"aclose", "env"} for name in public
    ), public
