"""Routing: which channels an event goes to under the notifications config, and when.

Pure functions over the config and a few counts from the database, so every rule (first
matching rule wins, mutes, repeat throttle, daily cap, quiet hours) is testable without a
clock or a network. The caller stores one `alert` row per decision, including suppressions,
so a missing alert can always be explained.
"""

import datetime as dt
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from steward.config.notifications import NotificationsConfig


@dataclass(frozen=True)
class AlertRequest:
    kind: str  # news, thesis_breach, test
    severity: int
    ticker: str | None
    event_type: str | None


@dataclass(frozen=True)
class RoutedAlert:
    channel: str
    status: str  # pending or suppressed
    send_after: dt.datetime
    suppressed_reason: str | None = None


@dataclass(frozen=True)
class RouteContext:
    now: dt.datetime
    # Non-test alerts sent or pending today (in the quiet-hours timezone).
    alerts_today: int = 0
    # Id of an alert for the same holding and event type inside the throttle window.
    recent_alert_id: int | None = None


def day_start(config: NotificationsConfig, now: dt.datetime) -> dt.datetime:
    """Midnight today in the notifications timezone: the daily cap's day."""
    tz = ZoneInfo(config.quiet_hours.timezone)
    local = now.astimezone(tz)
    return dt.datetime.combine(local.date(), dt.time(0), tzinfo=tz)


def route(
    request: AlertRequest, config: NotificationsConfig, ctx: RouteContext
) -> tuple[list[RoutedAlert], str]:
    """The alerts to store and a one-line explanation. No rows: the event waits for the
    digest (no rule matched)."""
    rule = config.matching_rule(request.severity, request.event_type, request.ticker)
    if rule is None:
        return [], f"no notification rule matches severity {request.severity}; digest only"
    explanation = f"rule {rule.id}"

    def suppressed(reason: str) -> tuple[list[RoutedAlert], str]:
        rows = [RoutedAlert(c, "suppressed", ctx.now, reason) for c in rule.channels]
        return rows, f"{explanation}: suppressed ({reason})"

    is_test = request.kind == "test"
    if request.ticker and request.ticker in config.muted_tickers:
        return suppressed(f"{request.ticker} is muted")
    if request.event_type and request.event_type in config.muted_event_types:
        return suppressed(f"{request.event_type} is muted")
    if not is_test and ctx.recent_alert_id is not None:
        return suppressed(
            f"repeat of alert #{ctx.recent_alert_id} within {config.repeat_throttle_hours} h"
        )
    if (
        not is_test
        and config.daily_alert_cap is not None
        and ctx.alerts_today >= config.daily_alert_cap
    ):
        return suppressed(f"daily cap of {config.daily_alert_cap} alerts reached")

    quiet_end = config.quiet_hours.end_if_quiet(ctx.now)
    if quiet_end is None or rule.during_quiet_hours == "send":
        rows = [RoutedAlert(c, "pending", ctx.now) for c in rule.channels]
        return rows, f"{explanation}: sending now to {', '.join(rule.channels)}"
    if rule.during_quiet_hours == "digest_only":
        return suppressed("quiet hours; digest only")
    send_after = quiet_end.astimezone(dt.UTC)
    rows = [RoutedAlert(c, "pending", send_after) for c in rule.channels]
    return rows, (
        f"{explanation}: quiet hours, held until {quiet_end.strftime('%H:%M %Z')} "
        f"for {', '.join(rule.channels)}"
    )
