"""Country mapping of geographic revenue: only exact country names, never over the owner."""

from decimal import Decimal
from types import SimpleNamespace
from typing import Any, cast

import asyncpg
import pytest

from steward.company import segment_autofill as sa
from steward.fundamentals.read import HoldingSegments, SegmentRow
from tests.conftest import MigratedDatabase


def row(label: str, subtotal: bool = False) -> SegmentRow:
    return SegmentRow(label=label, member=None, value=Decimal(100), share=0.1, subtotal=subtotal)


def test_only_exact_countries_are_mapped() -> None:
    maps = sa.country_maps(
        [
            row("United States of America"),
            row("Germany"),
            row("Europe"),
            row("Rest of World"),
            row("Eliminations"),
            row("France", subtotal=True),
        ],
        taken=set(),
    )
    assert [(m.member_label, m.value) for m in maps] == [
        ("United States of America", "United States"),
        ("Germany", "Germany"),
    ]


def test_a_member_with_a_country_row_is_left_alone() -> None:
    assert sa.country_maps([row("Germany")], taken={"germany"}) == []


@pytest.mark.db
async def test_filled_once_and_a_removed_mapping_is_not_restored(
    backend_pool: asyncpg.Pool, migrated_db: MigratedDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    admin = await asyncpg.connect(migrated_db.admin_url)
    try:
        owner = await admin.fetchval(
            "insert into auth.users (id, email) values (gen_random_uuid(), 'o@example.com')"
            " returning id"
        )
    finally:
        await admin.close()

    async def segments(_pool: Any, _ticker: str, _basis: Any) -> HoldingSegments:
        return cast(HoldingSegments, SimpleNamespace(geographic=[row("Germany"), row("Japan")]))

    monkeypatch.setattr(sa, "holding_segments", segments)
    first = await sa.fill_country_maps(backend_pool, owner, ["SAP_DE_EQ"])
    assert first["mapped"] == {"SAP_DE_EQ": ["Germany", "Japan"]}

    # The owner removes Japan (share 0); neither it nor Germany is written again.
    await backend_pool.execute(
        "insert into public.company_segment_map (owner_id, ticker, breakdown, member_label,"
        " context, value, share) values ($1, 'SAP_DE_EQ', 'geographic', 'Japan', 'country',"
        " 'Japan', 0)",
        owner,
    )
    assert (await sa.fill_country_maps(backend_pool, owner, ["SAP_DE_EQ"]))["mapped"] == {}
