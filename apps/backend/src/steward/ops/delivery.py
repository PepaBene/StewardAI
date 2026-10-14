"""Did the alert arrive (autonomous engine plan, L6)? The receipts are the alert row (`sent` only
after the channel's API accepted the message, with the provider's message id), Resend's own last
event for emails (a bounce or complaint is not a delivery) and the delivery record of each digest.

A channel that keeps failing cannot tell the owner through itself. The notice goes to Needs-you in
the app and, when another channel is configured, once through that one as well.
"""

import datetime as dt
import logging

import asyncpg

from steward.notify.channels import Channel, ChannelError, EmailChannel
from steward.ops import tasks

logger = logging.getLogger("steward.ops")

KIND = "delivery"
LOOKBACK = dt.timedelta(hours=24)
STALE_AFTER = dt.timedelta(hours=1)  # a pending alert this long past send_after was missed
BAD_EMAIL_EVENTS = ("bounced", "complained")
POLL_LIMIT = 30


def problem(failed: int, stale: int, bounced: int = 0) -> str | None:
    parts = []
    if failed:
        parts.append(f"{failed} failed to send")
    if bounced:
        parts.append(f"{bounced} bounced or were marked as spam")
    if stale:
        parts.append(f"{stale} still waiting long after their time")
    return " and ".join(parts) or None


async def poll_email(pool: asyncpg.Pool, email: EmailChannel, now: dt.datetime) -> int:
    """Ask Resend what happened to the emails sent in the last day that are not yet known to be
    delivered. Returns how many it checked."""
    rows = await pool.fetch(
        "select id, provider_id from public.alert where channel = 'email' and status = 'sent'"
        " and provider_id is not null and sent_at >= $1"
        " and provider_status is distinct from 'delivered' order by sent_at desc limit $2",
        now - LOOKBACK,
        POLL_LIMIT,
    )
    for r in rows:
        event = await email.status(r["provider_id"])
        await pool.execute(
            "update public.alert set provider_status = coalesce($2, provider_status),"
            " provider_checked_at = $3 where id = $1",
            r["id"],
            event,
            now,
        )
    return len(rows)


async def _counts(pool: asyncpg.Pool, now: dt.datetime) -> dict[str, tuple[int, int, int]]:
    """Per channel: (failed, stale, bounced) over the look-back window."""
    since = now - LOOKBACK
    found: dict[str, list[int]] = {}
    for r in await pool.fetch(
        "select channel,"
        " count(*) filter (where status = 'failed') as failed,"
        " count(*) filter (where status = 'pending' and send_after < $2) as stale,"
        " count(*) filter (where provider_status = any($3::text[])) as bounced"
        " from public.alert where created_at >= $1 group by channel",
        since,
        now - STALE_AFTER,
        list(BAD_EMAIL_EVENTS),
    ):
        found[r["channel"]] = [r["failed"], r["stale"], r["bounced"]]
    for r in await pool.fetch(
        "select ch.key as channel, count(*) as failed from public.report r,"
        " jsonb_each(r.delivery) ch where r.delivery is not null and r.created_at >= $1"
        " and ch.value->>'status' = 'failed' group by ch.key",
        since,
    ):
        found.setdefault(r["channel"], [0, 0, 0])[0] += r["failed"]
    return {k: (v[0], v[1], v[2]) for k, v in found.items()}


async def check(
    pool: asyncpg.Pool,
    now: dt.datetime,
    channels: dict[str, Channel] | None = None,
) -> dict[str, str]:
    """Open a notice per channel with undelivered messages; resolve it once they are gone.
    Returns the problem found per channel."""
    channels = channels or {}
    email = channels.get("email")
    if isinstance(email, EmailChannel):
        await poll_email(pool, email, now)
    found: dict[str, str] = {}
    for channel, (failed, stale, bounced) in (await _counts(pool, now)).items():
        text = problem(failed, stale, bounced)
        if text:
            found[channel] = text
    for channel, text in found.items():
        already = await pool.fetchval(
            "select exists (select 1 from public.ops_task where kind = $1 and subject = $2"
            " and status <> 'resolved')",
            KIND,
            channel,
        )
        await tasks.see(
            pool,
            KIND,
            channel,
            title=f"Messages are not reaching you by {channel}",
            detail=f"In the last day, {text}. Check the {channel} settings and the alert trail.",
            href="/disclosures/alerts",
            weight=1.0,
            status="needs_owner",
        )
        if not already:
            await _tell_another_way(channels, channel, text)
    await pool.execute(
        "update public.ops_task set status = 'resolved', resolved_at = now()"
        " where kind = $1 and status <> 'resolved' and subject <> all($2::text[])",
        KIND,
        list(found),
    )
    return found


async def _tell_another_way(channels: dict[str, Channel], broken: str, text: str) -> None:
    """Once, when the notice first opens: the same news through a channel that still works."""
    for name, channel in channels.items():
        if name == broken:
            continue
        try:
            await channel.send(
                f"Messages are not reaching you by {broken}",
                f"In the last day, {text}. Open the app for details.",
            )
        except ChannelError:
            logger.warning("could not warn through %s about %s", name, broken, exc_info=True)
        else:
            return
