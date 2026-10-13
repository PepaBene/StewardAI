"""Did the alert arrive (autonomous engine plan, L6)? The alert table is the receipt: a row is
`sent` only after the channel's API accepted the message. A channel that keeps failing, or alerts
that sit unsent long past their time, cannot tell the owner through that same channel, so the
notice goes to Needs-you in the app, which is the second channel.
"""

import datetime as dt

import asyncpg

from steward.ops import tasks

KIND = "delivery"
LOOKBACK = dt.timedelta(hours=24)
STALE_AFTER = dt.timedelta(hours=1)  # a pending alert this long past send_after was missed
MIN_PROBLEMS = 1


def problem(failed: int, stale: int) -> str | None:
    if failed + stale < MIN_PROBLEMS:
        return None
    parts = []
    if failed:
        parts.append(f"{failed} failed to send")
    if stale:
        parts.append(f"{stale} still waiting long after their time")
    return " and ".join(parts)


async def check(pool: asyncpg.Pool, now: dt.datetime) -> dict[str, str]:
    """Open a notice per channel with undelivered alerts; resolve it once they are gone.
    Returns the problem found per channel."""
    rows = await pool.fetch(
        "select channel,"
        " count(*) filter (where status = 'failed' and created_at >= $1) as failed,"
        " count(*) filter (where status = 'pending' and send_after < $2) as stale"
        " from public.alert where created_at >= $1 group by channel",
        now - LOOKBACK,
        now - STALE_AFTER,
    )
    found: dict[str, str] = {}
    for r in rows:
        text = problem(r["failed"], r["stale"])
        if text:
            found[r["channel"]] = text
    for channel, text in found.items():
        await tasks.see(
            pool,
            KIND,
            channel,
            title=f"Alerts are not reaching you by {channel}",
            detail=f"In the last day, {text}. Check the {channel} settings and the alert trail.",
            href="/disclosures/alerts",
            weight=1.0,
            status="needs_owner",
        )
    await pool.execute(
        "update public.ops_task set status = 'resolved', resolved_at = now()"
        " where kind = $1 and status <> 'resolved' and subject <> all($2::text[])",
        KIND,
        list(found),
    )
    return found
