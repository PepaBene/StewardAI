"""N8: every scheduled job type has an owner-only run-now route that enqueues it.

The six job types added after the first release (`returns_refresh`, `exchange_probes`,
`ir_releases`, `sec_fsds_backfill`, `pdmr_uk`, `graph_review`) had a schedule but no
`POST /jobs/...` route, unlike every older job."""

from collections.abc import AsyncIterator
from typing import Any

import asyncpg
import httpx
import pytest

from api.auth import AuthenticatedUser, require_owner
from api.db import get_pool, get_pool_or_error
from api.main import create_app
from steward.jobs import queue
from steward.jobs.registry import JOB_HANDLERS

OWNER = "7f0c3d4e-0000-4000-8000-000000000001"
NEW_JOBS = {
    "returns_refresh": "/jobs/returns-refresh",
    "exchange_probes": "/jobs/exchange-probes",
    "ir_releases": "/jobs/ir-releases",
    "sec_fsds_backfill": "/jobs/sec-fsds-backfill",
    "pdmr_uk": "/jobs/pdmr-uk",
    "graph_review": "/jobs/graph-review",
}


def _post_operations() -> dict[str, dict[str, Any]]:
    paths = create_app().openapi()["paths"]
    return {path: item["post"] for path, item in paths.items() if "post" in item}


def test_every_new_job_type_is_registered_and_has_a_run_now_route() -> None:
    assert set(NEW_JOBS) <= set(JOB_HANDLERS)
    assert set(NEW_JOBS.values()) <= set(_post_operations())


def test_run_now_routes_are_owner_only() -> None:
    operations = _post_operations()
    for path in NEW_JOBS.values():
        assert operations[path].get("security"), path  # the bearer scheme of `require_owner`


@pytest.fixture
async def api(
    backend_pool: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[httpx.AsyncClient]:
    async def fake_enqueue(conn: Any, job_id: int, job_type: str, **_: Any) -> int:
        return job_id  # pgmq is not required to prove what the route creates

    monkeypatch.setattr(queue, "enqueue", fake_enqueue)
    app = create_app()

    async def pool_override() -> asyncpg.Pool:
        return backend_pool

    app.dependency_overrides[get_pool] = pool_override
    app.dependency_overrides[get_pool_or_error] = pool_override
    app.dependency_overrides[require_owner] = lambda: AuthenticatedUser(OWNER, None, "aal2")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


@pytest.mark.db
@pytest.mark.parametrize(("job_type", "path"), sorted(NEW_JOBS.items()))
async def test_run_now_route_creates_a_manual_job(
    api: httpx.AsyncClient, backend_pool: asyncpg.Pool, job_type: str, path: str
) -> None:
    response = await api.post(path)
    assert response.status_code == 202, response.text
    assert response.json()["job_type"] == job_type and response.json()["trigger"] == "manual"
    row = await backend_pool.fetchrow(
        "select requested_by::text rb, params from public.job where id=$1", response.json()["id"]
    )
    assert row is not None and row["rb"] == OWNER


@pytest.mark.db
async def test_fsds_run_now_takes_a_validated_quarter(
    api: httpx.AsyncClient, backend_pool: asyncpg.Pool
) -> None:
    assert (await api.post("/jobs/sec-fsds-backfill", json={"quarter": "../x"})).status_code == 422
    ok = await api.post("/jobs/sec-fsds-backfill", json={"quarter": "2025q3", "ticker": "AAA"})
    assert ok.status_code == 202
    params = await backend_pool.fetchval(
        "select params from public.job where id=$1", ok.json()["id"]
    )
    assert '"quarter": "2025q3"' in str(params).replace("'", '"')
