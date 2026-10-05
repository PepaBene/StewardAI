"""Graph list / history / reconciliation routes with a fake pool (no database)."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from api.auth import AuthenticatedUser, require_owner
from api.db import get_pool
from api.graph_routes import router
from api.main import create_app
from steward.graph.reads import history_entries

NOW = datetime(2026, 10, 5, tzinfo=UTC)


class FakePool:
    def __init__(self) -> None:
        self.fetchrow_result: Any = None
        self.fetch_results: list[Any] = []
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    async def fetchval(self, sql: str, *args: Any) -> Any:
        self.calls.append((sql, args))
        return 1

    async def fetch(self, sql: str, *args: Any) -> Any:
        self.calls.append((sql, args))
        return self.fetch_results

    async def fetchrow(self, sql: str, *args: Any) -> Any:
        self.calls.append((sql, args))
        return self.fetchrow_result


@pytest.fixture
async def api() -> AsyncIterator[tuple[httpx.AsyncClient, FakePool]]:
    app = create_app()
    app.include_router(router)
    pool = FakePool()

    async def pool_override() -> Any:
        return pool

    app.dependency_overrides[get_pool] = pool_override
    app.dependency_overrides[require_owner] = lambda: AuthenticatedUser(
        "00000000-0000-0000-0000-000000000001", None, "aal2"
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client, pool


def test_history_entries_attach_current_decider_to_newest_entry() -> None:
    log = [
        {"changed_at": NOW, "from_status": None, "to_status": "suggested", "reason": None},
        {"changed_at": NOW, "from_status": "suggested", "to_status": "confirmed", "reason": "ok"},
    ]
    out = history_entries(log, "u1", "confirmed")
    assert [e["decided_by"] for e in out] == [None, "u1"]
    assert history_entries(log, "u1", "rejected")[-1]["decided_by"] is None
    assert history_entries([], "u1", "confirmed") == []


async def test_edge_list_passes_filters_and_returns_typed_rows(
    api: tuple[httpx.AsyncClient, FakePool],
) -> None:
    client, pool = api
    pool.fetch_results = [
        {
            "id": 1,
            "ticker": "AAPL",
            "kind": "supplier",
            "direction": "incoming",
            "counterparty": "TSMC",
            "object_key": "tsmc",
            "status": "suggested",
            "evidence_tier": "unverified",
            "source": "wikidata",
            "source_url": "https://x",
            "known_at": NOW,
        }
    ]
    r = await client.get(
        "/company-graph/edges",
        params={"status": "suggested", "ticker": "AAPL", "kind": "supplier", "limit": 5},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1 and body["rows"][0]["source_url"] == "https://x"
    assert pool.calls[-1][1] == ("suggested", "AAPL", "supplier", 5, 0)


async def test_edge_list_rejects_bad_status(api: tuple[httpx.AsyncClient, FakePool]) -> None:
    client, _ = api
    assert (await client.get("/company-graph/edges", params={"status": "nope"})).status_code == 422


async def test_history_404_and_found(api: tuple[httpx.AsyncClient, FakePool]) -> None:
    client, pool = api
    assert (await client.get("/company-graph/edges/9/decision-history")).status_code == 404
    pool.fetchrow_result = {
        "status": "confirmed",
        "decided_by": "u1",
        "reason": "r",
        "decided_at": NOW,
    }
    pool.fetch_results = [
        {"changed_at": NOW, "from_status": None, "to_status": "confirmed", "reason": "r"}
    ]
    r = await client.get("/company-graph/edges/9/decision-history")
    assert r.status_code == 200
    assert r.json()["entries"][0]["decided_by"] == "u1"


async def test_reconciliation_resolve_validates_reason_and_404(
    api: tuple[httpx.AsyncClient, FakePool],
) -> None:
    client, pool = api
    url = "/company-graph/reconciliations/3/resolve"
    short = await client.post(url, json={"resolution": "dismissed", "reason": "short"})
    assert short.status_code == 422
    ok_body = {"resolution": "resolved", "reason": "explained by the filed merger"}
    assert (await client.post(url, json=ok_body)).status_code == 404
    pool.fetchrow_result = {"id": 3, "ticker": "AAPL", "status": "accepted"}
    r = await client.post(url, json=ok_body)
    assert r.status_code == 200 and r.json()["status"] == "accepted"
    assert pool.calls[-1][1][1] == "accepted"


async def test_queue_items_carry_status(api: tuple[httpx.AsyncClient, FakePool]) -> None:
    client, pool = api
    pool.fetch_results = []
    r = await client.get("/relationship-review/queue")
    assert r.status_code == 200
    assert r.json() == {"filing_hits": [], "suggested_disclosures": [], "contradictions": []}
