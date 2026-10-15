"""Release gate upkeep: a feature that stays failed is switched off, a blip is not."""

import datetime as dt

import asyncpg
import pytest

from steward.ops import release_gate as rg
from steward.ops import tasks
from steward.release import activation, defects
from steward.release.readiness import FEATURES
from tests.conftest import MigratedDatabase


def now() -> dt.datetime:
    return dt.datetime.now(dt.UTC) + dt.timedelta(seconds=5)


async def owner_with_active_feature(
    pool: asyncpg.Pool, migrated_db: MigratedDatabase
) -> tuple[str, str]:
    admin = await asyncpg.connect(migrated_db.admin_url)
    try:
        owner = str(
            await admin.fetchval(
                "insert into auth.users (id, email) values (gen_random_uuid(), 'o@example.com')"
                " returning id"
            )
        )
    finally:
        await admin.close()
    feature = FEATURES[0].id
    # An empty test database has no inputs, so the gate does not allow any feature.
    await activation.record(pool, owner, activation.ActivationIn(feature=feature, active=True))
    return owner, feature


async def is_active(pool: asyncpg.Pool, owner: str, feature: str) -> bool:
    return next(
        a for a in await activation.list_current(pool, owner) if a.feature == feature
    ).active


@pytest.mark.db
async def test_a_failure_inside_the_retry_window_only_watches(
    backend_pool: asyncpg.Pool, migrated_db: MigratedDatabase
) -> None:
    owner, feature = await owner_with_active_feature(backend_pool, migrated_db)
    out = await rg.check_release_gate(backend_pool, owner, now(), retry_hours=24)
    assert out["failing"] == [feature] and out["switched_off"] == []
    assert await is_active(backend_pool, owner, feature)
    assert await tasks.needs_owner(backend_pool) == []


@pytest.mark.db
async def test_a_lasting_failure_switches_the_feature_off_and_the_notice_stays_until_dismissed(
    backend_pool: asyncpg.Pool, migrated_db: MigratedDatabase
) -> None:
    owner, feature = await owner_with_active_feature(backend_pool, migrated_db)
    out = await rg.check_release_gate(backend_pool, owner, now(), retry_hours=0)
    assert out["switched_off"] == [feature]
    assert not await is_active(backend_pool, owner, feature)
    notice = (await tasks.needs_owner(backend_pool))[0]
    assert notice.kind == "release_gate" and notice.subject == feature
    opened = await defects.list_current(backend_pool, owner)
    assert [(d.feature, d.state) for d in opened] == [(feature, "open")]

    again = await rg.check_release_gate(backend_pool, owner, now(), retry_hours=0)
    assert again["switched_off"] == [] and again["resolved"] == 0  # off now, notice stays
    assert await tasks.dismiss(backend_pool, notice.id)
    assert await tasks.needs_owner(backend_pool) == []
