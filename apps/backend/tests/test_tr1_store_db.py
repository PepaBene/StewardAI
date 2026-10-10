"""Stored UK TR-1 notices: written once by announcement, unknown figures stay null."""

import datetime as dt

import asyncpg
import pytest

from steward.disclosure.tr1_store import store_tr1_notice
from steward.sources.tr1_uk import parse_tr1_notice
from tests.test_tr1_uk import DISPOSAL

pytestmark = pytest.mark.db

ANNOUNCED = dt.datetime(2026, 4, 30, 7, 0, tzinfo=dt.UTC)
URL = "https://rns.example/tr1/1"


async def test_a_notice_is_stored_once_and_keeps_unknown_figures_null(
    backend_pool: asyncpg.Pool,
) -> None:
    notice = parse_tr1_notice(DISPOSAL, ANNOUNCED)
    assert notice is not None
    assert await store_tr1_notice(backend_pool, notice, source_url=URL, issuer_ticker="EWP")
    assert not await store_tr1_notice(backend_pool, notice, source_url=URL, issuer_ticker="EWP")
    row = await backend_pool.fetchrow("select * from public.uk_tr1_notice where source_url=$1", URL)
    assert row is not None and row["known_at"] == ANNOUNCED
    assert row["holder"] == notice.holder and row["review_status"] == "unreviewed"
    assert row["percent_total_after"] == notice.percent_total_after
    assert await backend_pool.fetchval("select count(*) from public.uk_tr1_notice") == 1
