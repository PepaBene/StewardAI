"""Regression tests for the defects a live browser pass found (items numbered as in the audit)."""

import datetime as dt
from decimal import Decimal

import asyncpg
import pytest

from steward.macro.read import context_for_holding
from tests.test_macro_fixes import _holding, _issuer, _macro


# Item 1: a mapping entered today must be visible today (created_at compared as a timestamp).
@pytest.mark.db
async def test_mapping_entered_today_is_visible_in_todays_context(
    backend_pool: asyncpg.Pool,
) -> None:
    issuer = await _issuer(backend_pool)
    await _holding(backend_pool, "TODAY", issuer)
    await backend_pool.execute(
        "insert into exposure_mapping (issuer_id, segment, geography, basis, weight, valid_from)"
        " values ($1,'s','DE','reported',0.5,date '2026-01-01')",
        issuer,
    )
    await _macro(backend_pool, "DE", "2026-06-01", "110", 0, "2026-07-10")
    # Entered at 10:00 on the as-of day: the old date-typed comparison saw midnight and hid it.
    ten = dt.datetime(2026, 9, 1, 10, tzinfo=dt.UTC)
    await backend_pool.execute("update exposure_mapping set created_at=$1", ten)
    seen = await context_for_holding(backend_pool, "TODAY", ten + dt.timedelta(hours=1))
    assert not seen.exposure_unknown and {o.geography for o in seen.observations} == {"DE"}
    assert seen.exposure["mapped"] == Decimal("0.5")
    before = await context_for_holding(backend_pool, "TODAY", ten - dt.timedelta(hours=1))
    assert before.exposure_unknown  # not yet entered at that instant
