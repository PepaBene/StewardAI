"""The digest lists public-disclosure alerts raised in its window."""

import datetime as dt

import asyncpg
import pytest

from steward.disclosure import alerts
from steward.news.digest import disclosure_alert_lines

pytestmark = pytest.mark.db


async def test_only_alerts_raised_in_the_window_are_listed(backend_pool: asyncpg.Pool) -> None:
    known = dt.datetime(2026, 7, 1, tzinfo=dt.UTC)
    for n in range(2):
        await alerts.record(
            backend_pool,
            kind="insider_trade",
            ticker="AAA",
            dedupe_key=f"d:{n}",
            headline=f"Insider bought {n}",
            detail={},
            known_at=known,
            throttle=None,
        )
    await backend_pool.execute(
        "update public.disclosure_alert set created_at = now() - interval '3 days' "
        "where dedupe_key = 'd:0'"
    )
    now = dt.datetime.now(dt.UTC)
    lines = await disclosure_alert_lines(backend_pool, now - dt.timedelta(days=1), now)
    assert [line.headline for line in lines] == ["Insider bought 1"]
