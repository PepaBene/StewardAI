"""Jobs config: when each scheduled job runs, and whether it runs at all."""

from typing import Annotated, Literal, Self
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


ModelId = Annotated[str, Field(pattern=r"^claude-[a-z0-9.-]{1,64}$", examples=["claude-sonnet-5"])]


class EarningsCheckSettings(ConfigModel):
    """The earnings thesis check: find the release, extract figures, validate, judge KPIs."""

    extraction_model: ModelId = "claude-sonnet-5"
    locate_model: ModelId = Field(
        default="claude-sonnet-5",
        description="Searches for the release when EDGAR has none (non-US issuers).",
    )
    effort: Literal["low", "medium", "high", "xhigh", "max"] = "high"
    max_steps: int = Field(
        default=8, ge=2, le=50, description="Claude calls plus document fetches per run."
    )
    max_searches: int = Field(default=3, ge=0, le=20, description="Web searches per run.")
    check_days_after_report: list[Annotated[int, Field(ge=0, le=30)]] = Field(
        default_factory=lambda: [1, 2, 4],
        description="Days after the report date on which the check runs until it finds the "
        "release (the spec's +1, retry at +2 and +4).",
    )
    max_document_mb: int = Field(default=20, ge=1, le=32)


class EarningsCalendarSettings(ConfigModel):
    """Next report dates: Finnhub for US issuers, the IR calendar page otherwise."""

    model: ModelId = Field(
        default="claude-haiku-4-5", description="Reads report dates off IR calendar pages."
    )
    lookahead_days: int = Field(default=120, ge=7, le=400)
    refresh_within_days: int = Field(
        default=14,
        ge=1,
        le=120,
        description="The weekly run refreshes holdings reporting within this many days (and "
        "those with no date); the quarterly run refreshes every holding.",
    )


class NewsSettings(ConfigModel):
    """The news pipeline after ingest: classification, corroboration."""

    classification_model: ModelId = "claude-haiku-4-5"
    classification_batch_size: int = Field(
        default=50, ge=5, le=100, description="Items per classification call."
    )
    corroboration_model: ModelId = "claude-haiku-4-5"
    corroboration_max_searches: int = Field(
        default=3, ge=0, le=10, description="Web searches per corroboration call."
    )
    corroboration_rounds: int = Field(
        default=2,
        ge=0,
        le=5,
        description="Corroboration searches per story: one when it is first seen, the rest "
        "spread over the re-check window.",
    )
    recheck_hours: int = Field(
        default=48, ge=1, le=168, description="How long an unconfirmed story is re-checked."
    )
    max_items_per_run: int = Field(
        default=500, ge=10, le=5000, description="Classification cap per run (cost guard)."
    )


class DigestSettings(ConfigModel):
    model: ModelId = "claude-sonnet-5"
    use_batch: bool = Field(
        default=True, description="Write the summary through the Batch API (half price)."
    )
    batch_wait_minutes: int = Field(
        default=60,
        ge=1,
        le=720,
        description="How long to wait for the batch before writing the summary directly.",
    )
    min_severity_summarised: int = Field(
        default=2,
        ge=1,
        le=3,
        description="Stories at or above this severity get a written summary; the rest are "
        "listed by headline.",
    )


class JobsConfig(ConfigModel):
    """Schedules for the worker's timed jobs. Stored as `config_version` rows, domain `jobs`.

    The worker re-reads this every few minutes, so a saved change applies without a restart.
    """

    schema_version: Literal[1] = 1
    portfolio_sync: JobSchedule = Field(
        default_factory=lambda: JobSchedule(cron="30 22 * * *"),
        description="Trading212 sync, FX rates and drift. Default: daily at 22:30 Prague time.",
    )

    earnings_calendar: JobSchedule = Field(
        default_factory=lambda: JobSchedule(cron="0 6 * * 0"),
        description="Refresh the report date of holdings reporting soon (see "
        "earnings_calendar_settings.refresh_within_days). Default: Sundays 06:00.",
    )
    earnings_calendar_full: JobSchedule = Field(
        default_factory=lambda: JobSchedule(cron="0 5 1 1,4,7,10 *"),
        description="Refresh every holding's report date from its IR calendar. Default: "
        "quarterly, 1 Jan/Apr/Jul/Oct 05:00.",
    )
    earnings_due: JobSchedule = Field(
        default_factory=lambda: JobSchedule(cron="0 8 * * *"),
        description="Queue earnings checks for holdings that reported. Default: daily 08:00.",
    )
    fact_reconcile: JobSchedule = Field(
        default_factory=lambda: JobSchedule(cron="0 9 * * *"),
        description="Check stored results figures against the issuers' 10-Q/10-K XBRL. "
        "Default: daily 09:00.",
    )
    news_ingest: JobSchedule = Field(
        default_factory=lambda: JobSchedule(cron="0 6-22/2 * * *"),
        description="Ingest, classify, corroborate and alert. Default: every 2 h, 06:00-22:00.",
    )
    daily_digest: JobSchedule = Field(
        default_factory=lambda: JobSchedule(cron="0 7 * * 1-5"),
        description="Daily digest of the last 24 h. Default: weekdays 07:00.",
    )
    weekly_digest: JobSchedule = Field(
        default_factory=lambda: JobSchedule(cron="0 18 * * 0"),
        description="Weekly digest of the last 7 days. Default: Sundays 18:00.",
    )
    earnings_check: EarningsCheckSettings = Field(default_factory=EarningsCheckSettings)
    earnings_calendar_settings: EarningsCalendarSettings = Field(
        default_factory=EarningsCalendarSettings
    )
    news: NewsSettings = Field(default_factory=NewsSettings)
    digest: DigestSettings = Field(default_factory=DigestSettings)

    def schedules(self) -> dict[str, JobSchedule]:
        return {
            "portfolio_sync": self.portfolio_sync,
            "earnings_calendar": self.earnings_calendar,
            "earnings_calendar_full": self.earnings_calendar_full,
            "earnings_due": self.earnings_due,
            "fact_reconcile": self.fact_reconcile,
            "news_ingest": self.news_ingest,
            "daily_digest": self.daily_digest,
            "weekly_digest": self.weekly_digest,
        }
