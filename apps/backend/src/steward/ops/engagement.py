"""What the owner opens (autonomy plan E2): the signal that lets the app send less of what is
never read.

`record` stores one row per open (no content). `read_rate` answers, for one kind, how many of
the items sent in a window were opened at least once. Tuning that uses it must stay bounded
and revertable, like the other tuners in `ops/tuning.py`.
"""

import datetime as dt
from typing import Literal

import asyncpg

Kind = Literal["news_story", "report", "notice", "escalation", "thesis", "company", "fund"]


async def record(pool: asyncpg.Pool, kind: Kind, ref: str, ticker: str | None = None) -> None:
    await pool.execute(
        "insert into public.engagement_event (kind, ref, ticker) values ($1, $2, $3)",
        kind,
        ref[:120],
        ticker,
    )


async def opened_refs(pool: asyncpg.Pool, kind: Kind, since: dt.datetime) -> set[str]:
    rows = await pool.fetch(
        "select distinct ref from public.engagement_event where kind = $1 and created_at >= $2",
        kind,
        since,
    )
    return {r["ref"] for r in rows}


def read_rate(sent: int, opened: int) -> float | None:
    """Share of the items sent that were opened; None when nothing was sent."""
    return None if sent <= 0 else min(opened, sent) / sent
