"""Alert rows: create them from a routing decision, and send the ones that are due.

`dispatch_due` is the only code that sends an alert. The worker calls it every few seconds,
and the news pipeline and the test-alert endpoint's job call it right after creating alerts,
so an alert outside quiet hours goes out within seconds and a held one goes out when quiet
hours end. Each alert is locked while it is sent (FOR UPDATE SKIP LOCKED), so two processes
never send the same alert twice.
"""

import datetime as dt
import logging
from collections.abc import Collection
from dataclasses import dataclass, field

import asyncpg

from steward.config.notifications import NotificationsConfig
from steward.notify.channels import (
    MISSING_CHANNEL_HELP,
    Channel,
    ChannelError,
    EmailChannel,
    configured_channels,
)
from steward.notify.route import AlertRequest, RouteContext, day_start, quiet_until, route
from steward.settings import get_settings

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
    dedupe_key: str | None = None,
    breach_id: int | None = None,
    available_channels: Collection[str] | None = None,
) -> CreatedAlerts:
    """`available_channels`: the channels that can deliver (None: those whose credentials
    are set in the process settings). A rule naming only missing channels falls back to them."""
    # The daily cap counts events, not rows: one event sent to two channels counts once.
    # Rows of one event share its story, verdict or dedupe key, else its creation call.
    alerts_today = await conn.fetchval(
        "select count(distinct coalesce('c' || cluster_id::text, 'v' || verdict_id::text,"
        " 'k' || dedupe_key, concat_ws(':', kind, ticker, event_type, created_at::text)))"
        " from public.alert where kind <> 'test'"
        " and status in ('sent', 'pending') and created_at >= $1",
        day_start(config, now),
    )
    recent = None
    if request.ticker and request.event_type and config.repeat_throttle_hours > 0:
        # Another story of the same holding and event type. A thesis breach repeats only
        # for the same KPI: a check that breaches two KPIs alerts both.
        recent = await conn.fetchval(
            "select a.id from public.alert a where a.kind = $1 and a.ticker = $2"
            " and a.event_type = $3 and a.status in ('sent', 'pending') and a.created_at >= $4"
            " and ($5::bigint is null or a.cluster_id is distinct from $5)"
            " and ($6::bigint is null or (a.verdict_id is distinct from $6 and exists ("
            "   select 1 from public.verdict v1 join public.verdict v2 on v2.kpi_id = v1.kpi_id"
            "   where v1.id = a.verdict_id and v2.id = $6)))"
            " order by a.id desc limit 1",
            request.kind,
            request.ticker,
            request.event_type,
            now - dt.timedelta(hours=config.repeat_throttle_hours),
            cluster_id,
            verdict_id,
        )
    if available_channels is None:
        available_channels = configured_channels(get_settings())
    holidays: set[dt.date] = set()
    if "holidays" in config.quiet_all_day_on:
        holidays = {
            r["date"]
            for r in await conn.fetch(
                "select date from public.holiday where calendar = $1 and scope = 'national'"
                " and date between $2 and $3",
                config.holiday_country,
                now.date() - dt.timedelta(days=1),
                now.date() + dt.timedelta(days=14),
            )
        }
    rows, explanation = route(
        request,
        config,
        RouteContext(
            now=now,
            alerts_today=alerts_today,
            recent_alert_id=recent,
            quiet_until=quiet_until(config, now, holidays),
            available=frozenset(available_channels),
        ),
    )
    created = CreatedAlerts(explanation=explanation)
    for row in rows:
        alert_id = await conn.fetchval(
            "insert into public.alert (kind, severity, ticker, event_type, cluster_id,"
            " verdict_id, channel, message, status, send_after, suppressed_reason,"
            " config_version_id, job_id, created_at, dedupe_key, breach_id)"
            " values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16)"
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
            dedupe_key,
            breach_id,
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


CRITICAL = 3


async def send_by_other_channel(
    conn: asyncpg.Connection, alert_id: int, channels: Collection[str], now: dt.datetime
) -> int | None:
    """A critical alert whose channel gave up goes out once more by another configured channel
    (a copy marked as such), so one dead channel cannot silence the alerts that matter most.
    Returns the new alert's id; None when it is not critical, no other channel exists, or the
    other channel already has this alert."""
    row = await conn.fetchrow("select * from public.alert where id = $1", alert_id)
    if row is None or row["severity"] < CRITICAL or row["kind"] == "test":
        return None
    other = sorted(c for c in channels if c != row["channel"])
    if not other:
        return None
    new_id: int | None = await conn.fetchval(
        "insert into public.alert (kind, severity, ticker, event_type, cluster_id, verdict_id,"
        " channel, message, status, send_after, config_version_id, job_id, created_at)"
        " select kind, severity, ticker, event_type, cluster_id, verdict_id, $2, $3, 'pending',"
        " $4, config_version_id, job_id, $4 from public.alert where id = $1"
        " on conflict do nothing returning id",
        alert_id,
        other[0],
        f"{row['channel'].capitalize()} did not deliver this alert; sent here instead.\n"
        + row["message"],
        now,
    )
    return new_id


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
                await send_by_other_channel(conn, row["id"], channels, moment)
                continue
            try:
                subject = subject_of(row["message"])
                if isinstance(channel, EmailChannel):
                    # A retry after a timeout must not send the email twice.
                    provider_id = await channel.send(
                        subject, row["message"], idempotency_key=f"steward-alert-{row['id']}"
                    )
                else:
                    provider_id = await channel.send(subject, row["message"])
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
                if final:
                    await send_by_other_channel(conn, row["id"], channels, moment)
                logger.warning("alert %s via %s failed: %s", row["id"], row["channel"], exc)
                continue
            await conn.execute(
                "update public.alert set status = 'sent', sent_at = clock_timestamp(),"
                " attempts = $2, error = null, provider_id = $3 where id = $1",
                row["id"],
                attempts,
                provider_id,
            )
            result.sent.append(row["id"])
    return result
