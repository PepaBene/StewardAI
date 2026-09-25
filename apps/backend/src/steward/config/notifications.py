"""Notifications config: which confirmed events reach which channel, and when.

Rules are checked in order and the first enabled rule that matches decides the channels, so
a narrow rule (one holding, one event type) goes above a broad one. An event no rule matches
waits for the digest. Every decision, including a suppression, is stored as an `alert` row.
"""

import datetime as dt
from typing import Literal, Self, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, model_validator

from steward.config.common import ConfigModel, Slug, Ticker, require_unique
from steward.config.event_rules import EventType

Channel = Literal["telegram", "email"]
CHANNELS: tuple[str, ...] = Channel.__args__  # type: ignore[attr-defined]


class RuleMatch(ConfigModel):
    severities: list[Literal[1, 2, 3]] = Field(min_length=1, examples=[[3]])
    event_types: list[EventType] = Field(default_factory=list, description="Empty: any type.")
    tickers: list[Ticker] = Field(default_factory=list, description="Empty: any holding.")

    def matches(self, severity: int, event_type: str | None, ticker: str | None) -> bool:
        if severity not in self.severities:
            return False
        if self.event_types and event_type not in self.event_types:
            return False
        return not (self.tickers and ticker not in self.tickers)


class NotificationRule(ConfigModel):
    id: Slug
    enabled: bool = True
    match: RuleMatch
    channels: list[Channel] = Field(
        min_length=1, description="Where a matching event is sent immediately."
    )
    during_quiet_hours: Literal["hold_until_end", "send", "digest_only"] = Field(
        default="hold_until_end",
        description="hold_until_end: send when quiet hours end. send: send anyway. "
        "digest_only: do not send; it appears in the next digest.",
    )

    @model_validator(mode="after")
    def _unique_channels(self) -> Self:
        require_unique("channel", self.channels)
        return self


def _hhmm(value: str) -> dt.time:
    return dt.time.fromisoformat(value)


class QuietHours(ConfigModel):
    enabled: bool = True
    start: str = Field(default="22:00", pattern=r"^([01][0-9]|2[0-3]):[0-5][0-9]$")
    end: str = Field(default="07:00", pattern=r"^([01][0-9]|2[0-3]):[0-5][0-9]$")
    timezone: str = Field(default="Europe/Prague", examples=["Europe/Prague"])

    @model_validator(mode="after")
    def _valid(self) -> Self:
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown timezone {self.timezone!r}") from exc
        if self.start == self.end:
            raise ValueError("quiet hours start and end must differ")
        return self

    def end_if_quiet(self, at: dt.datetime) -> dt.datetime | None:
        """If `at` falls inside quiet hours, the moment they end (timezone-aware); else None."""
        if not self.enabled:
            return None
        tz = ZoneInfo(self.timezone)
        local = at.astimezone(tz)
        start, end = _hhmm(self.start), _hhmm(self.end)
        now = local.time()
        overnight = start > end
        inside = (now >= start or now < end) if overnight else (start <= now < end)
        if not inside:
            return None
        end_day = local.date()
        if overnight and now >= start:
            end_day += dt.timedelta(days=1)
        return dt.datetime.combine(end_day, end, tzinfo=tz)


def _default_rules() -> list[NotificationRule]:
    return [
        NotificationRule(
            id="instant-severity-3",
            match=RuleMatch(severities=[3]),
            channels=["telegram"],
            during_quiet_hours="hold_until_end",
        )
    ]


class NotificationsConfig(ConfigModel):
    """Stored as `config_version` rows, domain `notifications`."""

    schema_version: Literal[1] = 1
    rules: list[NotificationRule] = Field(default_factory=_default_rules)
    quiet_hours: QuietHours = Field(default_factory=QuietHours)
    daily_alert_cap: int | None = Field(
        default=5,
        ge=1,
        le=100,
        description="Most alerts sent per day (in the quiet-hours timezone); the rest wait "
        "for the digest. Null: no cap.",
    )
    muted_tickers: list[Ticker] = Field(default_factory=list)
    muted_event_types: list[EventType] = Field(default_factory=list)
    repeat_throttle_hours: int = Field(
        default=24,
        ge=0,
        le=168,
        description="Another story with the same holding and event type within this many "
        "hours of an alert is not alerted again.",
    )
    digest_channels: list[Channel] = Field(
        default_factory=lambda: cast(list[Channel], ["email"]),
        description="Where the daily and weekly digests go.",
    )

    @model_validator(mode="after")
    def _unique(self) -> Self:
        require_unique("rule id", [r.id for r in self.rules])
        require_unique("digest channel", self.digest_channels)
        return self

    def matching_rule(
        self, severity: int, event_type: str | None, ticker: str | None
    ) -> NotificationRule | None:
        return next(
            (r for r in self.rules if r.enabled and r.match.matches(severity, event_type, ticker)),
            None,
        )
