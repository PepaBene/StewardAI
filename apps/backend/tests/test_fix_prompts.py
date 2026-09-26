"""Prompts config: the `prompts` domain, its API routes and the defaults list."""

from collections.abc import AsyncIterator
from typing import get_args

import asyncpg
import httpx
import pytest
from fastapi import FastAPI
from pydantic import ValidationError

from api.auth import AuthenticatedUser, require_owner
from api.db import get_pool, get_pool_or_error
from api.export_openapi import build_spec
from api.main import create_app
from steward.config import CONFIG_MODELS, PromptsConfig
from steward.config.prompts import PROMPTS, PromptKey, default_text, load_prompts
from steward.config.store import export_active, import_all, save_config

OWNER = "7f0c3d4e-0000-4000-8000-000000000001"
LONG = "You are a careful assistant. Answer in one short paragraph."


def _app() -> FastAPI:
    return create_app()


def test_prompts_is_a_config_domain() -> None:
    assert CONFIG_MODELS["prompts"] is PromptsConfig


def test_unknown_key_and_short_text_are_rejected() -> None:
    with pytest.raises(ValidationError):
        PromptsConfig.model_validate({"overrides": {"not_a_prompt": LONG}})
    with pytest.raises(ValidationError):
        PromptsConfig.model_validate({"overrides": {"chat_assistant": "too short"}})
    ok = PromptsConfig.model_validate({"overrides": {"chat_assistant": LONG}})
    assert ok.text("chat_assistant", "default") == LONG
    assert ok.text("claim_verifier", "default") == "default"


def test_every_prompt_key_has_metadata_and_a_default() -> None:
    assert set(PROMPTS) == set(get_args(PromptKey))
    for key in get_args(PromptKey):
        assert len(default_text(key)) >= 20


def test_the_openapi_schema_lists_the_prompts_routes() -> None:
    app = _app()
    spec = app.openapi()
    assert {"get", "put"} <= set(spec["paths"]["/config/prompts"])
    assert "get" in spec["paths"]["/config/prompts/defaults"]
    assert "post" in spec["paths"]["/config/prompts/validate"]
    keys = spec["components"]["schemas"]["PromptDefault"]["properties"]["key"]
    assert set(keys["enum"]) == set(get_args(PromptKey))
    history = spec["paths"]["/config/{domain}/versions"]["get"]["parameters"]
    domain = next(p for p in history if p["name"] == "domain")
    assert "prompts" in domain["schema"]["enum"]
    build_spec()  # the exported spec still builds


@pytest.fixture
async def api(backend_pool: asyncpg.Pool) -> AsyncIterator[httpx.AsyncClient]:
    app = _app()

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
async def test_defaults_list_covers_every_prompt(api: httpx.AsyncClient) -> None:
    res = await api.get("/config/prompts/defaults")
    assert res.status_code == 200
    items = res.json()
    assert [i["key"] for i in items] == list(get_args(PromptKey))
    for item in items:
        assert item["title"] == PROMPTS[item["key"]].title
        assert item["description"]
        assert item["default_text"] == default_text(item["key"])


@pytest.mark.db
async def test_save_and_load_roundtrip(api: httpx.AsyncClient, backend_pool: asyncpg.Pool) -> None:
    assert (await api.get("/config/prompts")).status_code == 404
    assert (await load_prompts(backend_pool)).overrides == {}

    body = {"schema_version": 1, "overrides": {"news_digest": LONG}}
    res = await api.put("/config/prompts", json=body, params={"note": "shorter digest"})
    assert res.status_code == 200, res.text
    first = res.json()
    assert first["body"]["overrides"] == {"news_digest": LONG}
    assert first["note"] == "shorter digest"

    got = await api.get("/config/prompts")
    assert got.status_code == 200 and got.json()["id"] == first["id"]
    loaded = await load_prompts(backend_pool)
    assert loaded.text("news_digest", default_text("news_digest")) == LONG
    assert loaded.text("chat_assistant", "x" * 20) == "x" * 20

    # Reset to default: the override is removed in a new version.
    res = await api.put(
        "/config/prompts",
        json={"overrides": {}},
        params={"base_version_id": first["id"]},
    )
    assert res.status_code == 200 and res.json()["id"] != first["id"]
    assert (await load_prompts(backend_pool)).overrides == {}

    # A save from the stale version conflicts.
    res = await api.put(
        "/config/prompts",
        json={"overrides": {"news_digest": LONG}},
        params={"base_version_id": first["id"]},
    )
    assert res.status_code == 409

    # The history routes know the domain.
    versions = await api.get("/config/prompts/versions")
    assert versions.status_code == 200 and len(versions.json()) == 2


@pytest.mark.db
async def test_invalid_bodies_are_rejected_with_422(api: httpx.AsyncClient) -> None:
    for body in (
        {"overrides": {"not_a_prompt": LONG}},
        {"overrides": {"chat_assistant": "too short"}},
        {"overrides": {"chat_assistant": "x" * 50_001}},
    ):
        assert (await api.put("/config/prompts", json=body)).status_code == 422
        assert (await api.post("/config/prompts/validate", json=body)).status_code == 422
    ok = await api.post("/config/prompts/validate", json={"overrides": {"chat_assistant": LONG}})
    assert ok.status_code == 200
    assert (await api.get("/config/prompts")).status_code == 404


@pytest.mark.db
async def test_export_and_import_include_prompts(backend_pool: asyncpg.Pool) -> None:
    async with backend_pool.acquire() as conn:
        await save_config(
            conn,
            "prompts",
            PromptsConfig(overrides={"claim_verifier": LONG}),
            created_by=OWNER,
        )
        exported = await export_active(conn)
        assert exported["prompts"]["body"]["overrides"] == {"claim_verifier": LONG}
        await import_all(
            conn,
            {"prompts": {"overrides": {"monthly_review": LONG}}},
            created_by=OWNER,
            note="import",
        )
    loaded = await load_prompts(backend_pool)
    assert loaded.overrides == {"monthly_review": LONG}
