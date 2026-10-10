"""The migration that removes Claude from saved configs (data only: new config versions)."""

import json
from pathlib import Path

import asyncpg
import pytest

MIGRATION = (
    Path(__file__).resolve().parents[3]
    / "supabase"
    / "migrations"
    / "20261101100000_remove_claude_models.sql"
)


async def put(pool: asyncpg.Pool, domain: str, body: dict[str, object]) -> int:
    await pool.execute("update public.config_version set active = false where domain = $1", domain)
    return int(
        await pool.fetchval(
            "insert into public.config_version (domain, body, schema_version, active)"
            " values ($1, $2::jsonb, 1, true) returning id",
            domain,
            json.dumps(body),
        )
    )


async def active(pool: asyncpg.Pool, domain: str) -> asyncpg.Record:
    row = await pool.fetchrow(
        "select id, body, parent_id from public.config_version where domain = $1 and active",
        domain,
    )
    assert row is not None
    return row


@pytest.mark.db
async def test_saved_configs_lose_their_claude_models(backend_pool: asyncpg.Pool) -> None:
    pool = backend_pool
    budget_id = await put(
        pool,
        "budget",
        {
            "downgrade_model": "claude-haiku-4-5",
            "prices": [
                {"model": "claude-opus-4-7", "input_per_mtok": 5, "output_per_mtok": 25},
                {"model": "gpt-6-luna", "input_per_mtok": 0.1, "output_per_mtok": 0.5},
            ],
        },
    )
    prompts_id = await put(
        pool,
        "prompts",
        {
            "overrides": {"chat_assistant": "a custom prompt of enough length"},
            "override_providers": {
                "chat_assistant": ["anthropic", "openai"],
                "digest": ["anthropic"],
            },
        },
    )
    clean_id = await put(pool, "verification", {"claim_verifier": {"model": "gpt-6.1-sol"}})

    await pool.execute(MIGRATION.read_text(encoding="utf-8"))

    budget = await active(pool, "budget")
    body = json.loads(budget["body"])
    assert budget["parent_id"] == budget_id
    assert body["downgrade_model"] == "gpt-6-luna"
    assert [p["model"] for p in body["prices"]] == ["gpt-6-luna"]

    prompts = await active(pool, "prompts")
    assert prompts["parent_id"] == prompts_id
    assert json.loads(prompts["body"])["override_providers"] == {"chat_assistant": ["openai"]}

    # A config that never named Claude is left alone, and the old versions stay as history.
    assert (await active(pool, "verification"))["id"] == clean_id
    old = await pool.fetchval("select active from public.config_version where id = $1", budget_id)
    assert old is False

    # Running it again changes nothing.
    await pool.execute(MIGRATION.read_text(encoding="utf-8"))
    assert (await active(pool, "budget"))["id"] == budget["id"]
