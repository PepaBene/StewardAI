"""Browser-pass item 5: a vintage source (ALFRED) is history for the series, not a second card."""

import datetime as dt
from decimal import Decimal

import asyncpg
import pytest

from steward.macro.read import context_for_holding
from tests.test_macro_fixes import _holding, _issuer

pytestmark = pytest.mark.db

AS_OF = dt.datetime(2026, 10, 3, tzinfo=dt.UTC)


async def _obs(
    pool: asyncpg.Pool, source: str, period: str, value: str, revision: int, known: str
) -> None:
    await pool.execute(
        "insert into macro_observation (series_id,source,geography,period,value,unit,frequency,"
        " known_at,release_at,vintage,revision,source_url) values ('us_unemployment',$1,'US',$2,$3,"
        " '%','monthly',$4,$5,$6,$7,'u')",
        source,
        dt.date.fromisoformat(period),
        Decimal(value),
        dt.datetime.fromisoformat(known).replace(tzinfo=dt.UTC),
        dt.datetime.fromisoformat(known).replace(tzinfo=dt.UTC) if source == "alfred" else None,
        f"v{revision}",
        revision,
    )


async def _setup(pool: asyncpg.Pool, *, with_fred: bool = True) -> None:
    issuer = await _issuer(pool)
    await _holding(pool, "HOLD", issuer)
    await pool.execute(
        "insert into exposure_mapping (issuer_id, segment, geography, basis, weight, valid_from)"
        " values ($1,'s','US','reported',1,date '2026-01-01')",
        issuer,
    )
    await pool.execute("update exposure_mapping set created_at='2026-02-01'")
    if with_fred:
        await _obs(pool, "fred", "2026-08-01", "4.3", 0, "2026-09-20")
    await _obs(pool, "alfred", "2026-08-01", "4.2", 0, "2026-09-05")
    await _obs(pool, "alfred", "2026-08-01", "4.3", 1, "2026-09-20")
    await pool.execute(
        "insert into macro_release_calendar(provider,series_id,calendar_key,release_date,"
        "source_url,known_at) values ('fred','us_unemployment','k','2026-11-06','https://x.test',"
        "'2026-10-01')"
    )


async def test_alfred_does_not_produce_a_second_card_and_vintages_become_history(
    backend_pool: asyncpg.Pool,
) -> None:
    await _setup(backend_pool)
    context = await context_for_holding(backend_pool, "HOLD", AS_OF, history=6)
    assert [(o.series_id, o.source) for o in context.observations] == [("us_unemployment", "fred")]
    card = context.observations[0]
    assert card.next_release == dt.date(2026, 11, 6)  # FRED's published date, one estimate only
    assert card.next_release_estimate is None
    (period,) = card.history
    assert period.value_first_release == Decimal("4.2") and period.revision_count == 1
    assert [e.source for e in context.series_effects] == []  # one change source at most
    assert all(c.source != "alfred" for c in context.comparisons)


async def test_a_vintage_only_series_keeps_its_card_with_the_primary_calendar(
    backend_pool: asyncpg.Pool,
) -> None:
    await _setup(backend_pool, with_fred=False)
    context = await context_for_holding(backend_pool, "HOLD", AS_OF)
    (card,) = context.observations
    assert card.source == "alfred" and card.next_release == dt.date(2026, 11, 6)
