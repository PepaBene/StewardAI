"""Case items drafted from the thesis: drafts only, once, and never over a removed item."""

import asyncpg
import pytest

from steward.company import case_autofill as ca
from steward.config.thesis import ThesesConfig, ThesisConfig
from tests.conftest import MigratedDatabase


def thesis() -> ThesisConfig:
    return ThesisConfig(
        ticker="AAPL_US_EQ",
        why_owned="Services grow and lift margins",
        pre_mortem=["Regulators force App Store changes"],
        exit_conditions=["Services growth under 8% for two years", "  "],
    )


def test_each_part_of_the_thesis_maps_to_a_kind() -> None:
    assert ca.drafts(thesis()) == [
        ("bull", "Services grow and lift margins"),
        ("bear", "Regulators force App Store changes"),
        ("risk", "Services growth under 8% for two years"),
    ]


@pytest.mark.db
async def test_drafted_once_and_a_removed_item_stays_removed(
    backend_pool: asyncpg.Pool, migrated_db: MigratedDatabase
) -> None:
    admin = await asyncpg.connect(migrated_db.admin_url)
    try:
        owner = await admin.fetchval(
            "insert into auth.users (id, email) values (gen_random_uuid(), 'o@example.com')"
            " returning id"
        )
    finally:
        await admin.close()
    theses = ThesesConfig(theses=[thesis()])
    held = {"AAPL_US_EQ"}

    assert (await ca.fill_case_items(backend_pool, owner, theses, set()))["drafted"] == {}
    assert (await ca.fill_case_items(backend_pool, owner, theses, held))["drafted"] == {
        "AAPL_US_EQ": 3
    }
    rows = await backend_pool.fetch("select status, confidence from public.company_case_item")
    assert {(r["status"], r["confidence"]) for r in rows} == {("draft", "low")}

    await backend_pool.execute(
        "update public.company_case_item set archived = true where kind = 'bear'"
    )
    assert (await ca.fill_case_items(backend_pool, owner, theses, held))["drafted"] == {}
