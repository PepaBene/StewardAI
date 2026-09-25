"""Jobs config: when each scheduled job runs, and whether it runs at all."""

from typing import Literal, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apscheduler.triggers.cron import CronTrigger
from pydantic import Field, model_validator

from steward.config.common import ConfigModel


class JobSchedule(ConfigModel):
    enabled: bool = True
    cron: str = Field(
        description="Standard 5-field cron expression (minute hour day month weekday).",
        examples=["30 22 * * *"],
    )
    timezone: str = Field(default="Europe/Prague", examples=["Europe/Prague"])

    @model_validator(mode="after")
    def _valid_schedule(self) -> Self:
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown timezone {self.timezone!r}") from exc
        try:
            self.trigger()
        except ValueError as exc:
            raise ValueError(f"invalid cron expression {self.cron!r}: {exc}") from exc
        return self

    def trigger(self) -> CronTrigger:
        return CronTrigger.from_crontab(self.cron, timezone=ZoneInfo(self.timezone))


class JobsConfig(ConfigModel):
    """Schedules for the worker's timed jobs. Stored as `config_version` rows, domain `jobs`.

    The worker re-reads this every few minutes, so a saved change applies without a restart.
    """

    schema_version: Literal[1] = 1
    portfolio_sync: JobSchedule = Field(
        default_factory=lambda: JobSchedule(cron="30 22 * * *"),
        description="Trading212 sync, FX rates and drift. Default: daily at 22:30 Prague time.",
    )

    def schedules(self) -> dict[str, JobSchedule]:
        return {"portfolio_sync": self.portfolio_sync}
