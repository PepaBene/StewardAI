"""Group B fixes: request body size limit, bounded id params, storage transport errors,
chat cancel of an unknown action, and optimistic concurrency on config saves."""

from collections.abc import AsyncIterator, Iterator
from typing import Any

import asyncpg
import httpx
import pytest
from fastapi.testclient import TestClient

from api import main
from api.auth import AuthenticatedUser, require_owner
from api.db import get_pool, get_pool_or_error
from api.main import create_app
from steward.storage import StorageError, SupabaseStorage

OWNER = "7f0c3d4e-0000-4000-8000-000000000001"


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app()) as test_client:
        yield test_client


# --- body size limit -------------------------------------------------------------------


def test_oversized_body_is_rejected_before_auth(client: TestClient) -> None:
    body = b"x" * (main.MAX_BODY_BYTES + 1)
    response = client.post(
        "/config/strategy/validate", content=body, headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 413


def test_oversized_chunked_body_is_rejected(client: TestClient) -> None:
    def chunks() -> Iterator[bytes]:
        for _ in range(3):
            yield b"x" * main.MAX_BODY_BYTES

    response = client.post(
        "/config/strategy/validate", content=chunks(), headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 413


def test_small_body_still_reaches_auth(client: TestClient) -> None:
    response = client.post("/config/strategy/validate", json={"base_currency": "CZK"})
    assert response.status_code == 401


def test_import_route_has_a_larger_limit(client: TestClient) -> None:
    body = b'{"domains": {}, "pad": "' + b"x" * main.MAX_BODY_BYTES + b'"}'
    response = client.post(
        "/config/import", content=body, headers={"Content-Type": "application/json"}
    )
    assert response.status_code != 413  # past the size check
    too_big = b"x" * (main.MAX_BODY_BYTES_BY_PATH["/config/import"] + 1)
    response = client.post(
        "/config/import", content=too_big, headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 413


def test_health_stays_public(client: TestClient) -> None:
    assert client.get("/health").status_code == 200


# --- id bounds ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "/jobs/9223372036854775808",
        "/jobs?before_id=9223372036854775808",
        "/chat/conversations/9223372036854775808",
        "/reports/9223372036854775808",
        "/news?before_id=9223372036854775808",
        "/news?severity=40000",
        "/feedback?target_id=9223372036854775808",
        "/config/strategy/versions/9223372036854775808",
        "/config/strategy/diff?from=1&to=9223372036854775808",
        "/jobs/0",
    ],
)
def test_out_of_range_ids_are_422(url: str) -> None:
    app = create_app()

    # A pool that is never used: validation must fail before any query.
    app.dependency_overrides[get_pool] = lambda: object()
    app.dependency_overrides[require_owner] = lambda: AuthenticatedUser(OWNER, None, "aal2")
    with TestClient(app) as test_client:
        assert test_client.get(url).status_code == 422


# --- storage transport errors ----------------------------------------------------------


async def test_storage_transport_errors_become_storage_errors() -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(fail)) as http:
        storage = SupabaseStorage(http, "https://example.supabase.co", "sb_secret_x")
        with pytest.raises(StorageError):
            await storage.get("ab/abc.html")
        with pytest.raises(StorageError):
            await storage.signed_url("ab/abc.html", 60)
        with pytest.raises(StorageError):
            await storage.put("ab/abc.html", b"x", "text/html")


async def test_signing_response_that_is_not_json_is_a_storage_error() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _r: httpx.Response(200, text="<html>"))
    ) as http:
        storage = SupabaseStorage(http, "https://example.supabase.co", "sb_secret_x")
        with pytest.raises(StorageError):
            await storage.signed_url("ab/abc.html", 60)


# --- database-backed ---------------------------------------------------------------------


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
    ) as test_client:
        yield test_client


@pytest.mark.db
async def test_cancel_unknown_action_is_404(api: httpx.AsyncClient) -> None:
    response = await api.post("/chat/actions/987654/cancel")
    assert response.status_code == 404


def _strategy(weight: float) -> dict[str, Any]:
    return {
        "base_currency": "CZK",
        "target_positions": [{"ticker": "AAPL_US_EQ", "target_weight": weight, "band": 0.02}],
    }


@pytest.mark.db
async def test_config_save_with_stale_base_version_is_409(api: httpx.AsyncClient) -> None:
    first = await api.put("/config/strategy", json=_strategy(0.1))
    assert first.status_code == 200
    base = first.json()["id"]
    second = await api.put(f"/config/strategy?base_version_id={base}", json=_strategy(0.2))
    assert second.status_code == 200, second.text
    stale = await api.put(f"/config/strategy?base_version_id={base}", json=_strategy(0.3))
    assert stale.status_code == 409
    assert "reload" in stale.json()["detail"]
    # Without a base version the save goes through as before.
    assert (await api.put("/config/strategy", json=_strategy(0.3))).status_code == 200


@pytest.mark.db
async def test_rollback_with_stale_base_version_is_409(api: httpx.AsyncClient) -> None:
    first = (await api.put("/config/strategy", json=_strategy(0.1))).json()["id"]
    second = (await api.put("/config/strategy", json=_strategy(0.2))).json()["id"]
    stale = await api.post(f"/config/strategy/versions/{first}/rollback?base_version_id={first}")
    assert stale.status_code == 409
    fresh = await api.post(f"/config/strategy/versions/{first}/rollback?base_version_id={second}")
    assert fresh.status_code == 200
