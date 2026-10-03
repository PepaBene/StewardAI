"""Browser-pass item 8: keyless public providers are collected by default and say why not."""

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import asyncpg
import pytest

from steward.config.jobs import JobsConfig
from steward.macro.public_collect import _skip_reason

ROOT = Path(__file__).resolve().parents[3] / "supabase"
NAME = "20261007170000_keyless_public_providers_enabled"


def test_keyless_public_providers_default_on_and_keyed_ones_stay_opt_in() -> None:
    providers = JobsConfig().providers
    assert [providers[n].enabled for n in ("world_bank", "oecd", "bis")] == [True] * 3
    assert not any(providers[n].enabled for n in ("alpha_vantage", "eodhd", "fmp", "un_comtrade"))


@pytest.mark.db
async def test_the_skip_reason_names_the_switch_that_is_shut(backend_pool: asyncpg.Pool) -> None:
    ctx: Any = SimpleNamespace(pool=backend_pool)
    jobs = JobsConfig()
    # The seeded gate row is available and the default flag is on: collected.
    assert await _skip_reason(ctx, "world_bank", "indicators", jobs.providers["world_bank"]) is None
    off = jobs.providers["oecd"].model_copy(update={"enabled": False})
    assert "providers.oecd.enabled is false" in str(await _skip_reason(ctx, "oecd", "sdmx", off))
    await backend_pool.execute(
        "update provider_access set state='paid_only' where provider='bis' and endpoint='sdmx'"
    )
    reason = await _skip_reason(ctx, "bis", "sdmx", jobs.providers["bis"])
    assert reason is not None and "access gate closed for bis/sdmx" in reason
    assert "paid_only" in reason


@pytest.mark.db
async def test_migration_enables_the_three_in_a_saved_config_and_the_rollback_restores_it(
    migrated_db: Any,
) -> None:
    jobs = JobsConfig()
    for name in ("world_bank", "oecd", "bis"):
        jobs.providers[name].enabled = False
    body = jobs.model_dump(mode="json")
    up = (ROOT / "migrations" / f"{NAME}.sql").read_text()
    down = (ROOT / "rollback" / f"{NAME}.down.sql").read_text()
    conn = await asyncpg.connect(migrated_db.admin_url)
    try:
        # A no-op without a saved jobs config.
        await conn.execute(up)
        assert await conn.fetchval("select count(*) from config_version where domain='jobs'") == 0
        first = await conn.fetchval(
            "insert into config_version (domain, body, schema_version, active)"
            " values ('jobs', $1::jsonb, 1, true) returning id",
            json.dumps(body),
        )
        await conn.execute(up)
        rows = await conn.fetch(
            "select id, parent_id, active, body from config_version where domain='jobs' order by id"
        )
        assert [r["active"] for r in rows] == [False, True] and rows[1]["parent_id"] == first
        active = JobsConfig.model_validate(json.loads(rows[1]["body"]))
        assert [active.providers[n].enabled for n in ("world_bank", "oecd", "bis")] == [True] * 3
        # Nothing but the three flags changed.
        changed = json.loads(rows[1]["body"])
        for name in ("world_bank", "oecd", "bis"):
            changed["providers"][name]["enabled"] = False
        assert changed == body
        await conn.execute(up)  # idempotent: nothing left to flip
        assert await conn.fetchval("select count(*) from config_version where domain='jobs'") == 2
        await conn.execute(down)
        rows = await conn.fetch(
            "select id, active from config_version where domain='jobs' order by id"
        )
        # Versions are immutable history: the original is active again, the added one inactive.
        assert [(r["id"] == first, r["active"]) for r in rows] == [(True, True), (False, False)]
    finally:
        await conn.close()
