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
    # Set on a day the config treats as quiet all day (weekend, public holiday): when the
    # quiet period ends.
    quiet_until: dt.datetime | None = None
    # The channels whose credentials are set; None when unknown (no fallback then).
    available: frozenset[str] | None = None


def pick_channels(
    named: list[str], available: frozenset[str] | None
) -> tuple[list[str], str | None]:
    """The rule's channels that can deliver. A rule naming only unconfigured channels falls
    back to the configured ones, so an alert is not lost to a missing token; with none
    configured the rule's channels stay and the alerts fail with the reason."""
    if available is None or not available:
        return list(named), None
    usable = [c for c in named if c in available]
    missing = [c for c in named if c not in available]
    if usable:
        note = f"{', '.join(missing)} not configured, skipped" if missing else None
        return usable, note
    fallback = sorted(available)
    return fallback, f"{', '.join(missing)} not configured; fell back to {', '.join(fallback)}"


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
    # A test alert checks the channels the rules name, so it never falls back.
    channels, note = pick_channels(
        list(rule.channels), None if request.kind == "test" else ctx.available
    )
    if note:
        explanation = f"{explanation} ({note})"

    def suppressed(reason: str) -> tuple[list[RoutedAlert], str]:
        rows = [RoutedAlert(c, "suppressed", ctx.now, reason) for c in channels]
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

    quiet_end = ctx.quiet_until or config.quiet_hours.end_if_quiet(ctx.now)
    if quiet_end is None or rule.during_quiet_hours == "send":
        rows = [RoutedAlert(c, "pending", ctx.now) for c in channels]
        return rows, f"{explanation}: sending now to {', '.join(channels)}"
    if rule.during_quiet_hours == "digest_only":
        return suppressed("quiet hours; digest only")
    send_after = quiet_end.astimezone(dt.UTC)
    rows = [RoutedAlert(c, "pending", send_after) for c in channels]
    return rows, (
        f"{explanation}: quiet hours, held until {quiet_end.strftime('%a %H:%M %Z')} "
        f"for {', '.join(channels)}"
    )


def quiet_day_end(
    config: NotificationsConfig, now: dt.datetime, holidays: set[dt.date]
) -> dt.datetime | None:
    """When today is a quiet day (weekend or holiday, as configured), the end of quiet hours
    on the next day that is not; else None."""
    if not config.quiet_all_day_on:
        return None
    tz = ZoneInfo(config.quiet_hours.timezone)

    def quiet(day: dt.date) -> bool:
        return ("weekends" in config.quiet_all_day_on and day.weekday() >= 5) or (
            "holidays" in config.quiet_all_day_on and day in holidays
        )

    day = now.astimezone(tz).date()
    if not quiet(day):
        return None
    while quiet(day):
        day += dt.timedelta(days=1)
    end = dt.time.fromisoformat(config.quiet_hours.end)
    return dt.datetime.combine(day, end, tzinfo=tz)


def quiet_until(
    config: NotificationsConfig, now: dt.datetime, holidays: set[dt.date]
) -> dt.datetime | None:
    """When an alert created now may go out: the end of a quiet day, or the end of tonight's
    quiet hours pushed past any quiet day it lands on (Friday 23:00 -> Monday 07:00, not
    Saturday 07:00). None: not quiet now."""
    whole_day = quiet_day_end(config, now, holidays)
    if whole_day is not None:
        return whole_day
    overnight = config.quiet_hours.end_if_quiet(now)
    if overnight is None:
        return None
    return quiet_day_end(config, overnight, holidays) or overnight
