"""Alert rows: create them from a routing decision, and send the ones that are due.

`dispatch_due` is the only code that sends an alert. The worker calls it every few seconds,
and the news pipeline and the test-alert endpoint's job call it right after creating alerts,
so an alert outside quiet hours goes out within seconds and a held one goes out when quiet
hours end. Each alert is locked while it is sent (FOR UPDATE SKIP LOCKED), so two processes
never send the same alert twice.
"""

import datetime as dt
import logging
from dataclasses import dataclass, field

import asyncpg

from steward.config.notifications import NotificationsConfig
from steward.notify.channels import MISSING_CHANNEL_HELP, Channel, ChannelError
from steward.notify.route import AlertRequest, RouteContext, day_start, route

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3


@dataclass
class CreatedAlerts:
    ids: list[int] = field(default_factory=list)
    explanation: str = ""
    channels: list[str] = field(default_factory=list)
    held_until: dt.datetime | None = None
    suppressed_reason: str | None = None


async def create_alerts(
    conn: asyncpg.Connection | asyncpg.Pool,
    request: AlertRequest,
    message: str,
    config: NotificationsConfig,
    *,
    now: dt.datetime,
    config_version_id: int | None = None,
    cluster_id: int | None = None,
    verdict_id: int | None = None,
    job_id: int | None = None,
) -> CreatedAlerts:
    alerts_today = await conn.fetchval(
        "select count(*) from public.alert where kind <> 'test'"
        " and status in ('sent', 'pending') and created_at >= $1",
        day_start(config, now),
    )
    recent = None
    if request.ticker and request.event_type and config.repeat_throttle_hours > 0:
        recent = await conn.fetchval(
            "select id from public.alert where kind = $1 and ticker = $2 and event_type = $3"
            " and status in ('sent', 'pending') and created_at >= $4"
            " and ($5::bigint is null or cluster_id is distinct from $5)"
            " order by id desc limit 1",
            request.kind,
            request.ticker,
            request.event_type,
            now - dt.timedelta(hours=config.repeat_throttle_hours),
            cluster_id,
        )
    rows, explanation = route(
        request, config, RouteContext(now=now, alerts_today=alerts_today, recent_alert_id=recent)
    )
    created = CreatedAlerts(explanation=explanation)
    for row in rows:
        alert_id = await conn.fetchval(
            "insert into public.alert (kind, severity, ticker, event_type, cluster_id,"
            " verdict_id, channel, message, status, send_after, suppressed_reason,"
            " config_version_id, job_id, created_at)"
            " values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)"
            " on conflict do nothing returning id",
            request.kind,
            request.severity,
            request.ticker,
            request.event_type,
            cluster_id,
            verdict_id,
            row.channel,
            message,
            row.status,
            row.send_after,
            row.suppressed_reason,
            config_version_id,
            job_id,
            now,
        )
        if alert_id is None:
            continue
        created.ids.append(alert_id)
        if row.status == "pending":
            created.channels.append(row.channel)
            if row.send_after > now:
                created.held_until = row.send_after
        else:
            created.suppressed_reason = row.suppressed_reason
    return created


@dataclass
class DispatchResult:
    sent: list[int] = field(default_factory=list)
    failed: list[int] = field(default_factory=list)
    retrying: list[int] = field(default_factory=list)


def subject_of(message: str) -> str:
    return message.splitlines()[0][:200] if message else "StewardAI alert"


async def dispatch_due(
    pool: asyncpg.Pool,
    channels: dict[str, Channel],
    *,
    now: dt.datetime | None = None,
    limit: int = 20,
) -> DispatchResult:
    result = DispatchResult()
    for _ in range(limit):
        moment = now or dt.datetime.now(dt.UTC)
        async with pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                "select id, channel, message, attempts from public.alert"
                " where status = 'pending' and send_after <= $1"
                " order by send_after, id limit 1 for update skip locked",
                moment,
            )
            if row is None:
                break
            attempts = row["attempts"] + 1
            channel = channels.get(row["channel"])
            if channel is None:
                await conn.execute(
                    "update public.alert set status = 'failed', attempts = $2, error = $3"
                    " where id = $1",
                    row["id"],
                    attempts,
                    MISSING_CHANNEL_HELP.get(row["channel"], "channel not configured"),
                )
                result.failed.append(row["id"])
                continue
            try:
                await channel.send(subject_of(row["message"]), row["message"])
            except ChannelError as exc:
                final = attempts >= MAX_ATTEMPTS
                await conn.execute(
                    "update public.alert set status = $2, attempts = $3, error = $4,"
                    " send_after = $5 where id = $1",
                    row["id"],
                    "failed" if final else "pending",
                    attempts,
                    str(exc)[:500],
                    moment + dt.timedelta(minutes=2**attempts),
                )
                (result.failed if final else result.retrying).append(row["id"])
                logger.warning("alert %s via %s failed: %s", row["id"], row["channel"], exc)
                continue
            await conn.execute(
                "update public.alert set status = 'sent', sent_at = clock_timestamp(),"
                " attempts = $2, error = null where id = $1",
                row["id"],
                attempts,
            )
            result.sent.append(row["id"])
    return result
