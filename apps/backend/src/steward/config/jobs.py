"""Jobs config: when each scheduled job runs, and whether it runs at all."""

import datetime as dt
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
        return cron_trigger(self.cron, ZoneInfo(self.timezone))


_DAY_NAMES = ("sun", "mon", "tue", "wed", "thu", "fri", "sat")


def crontab_weekdays(field: str) -> str:
    """Standard crontab weekdays (0 or 7 = Sunday, 1 = Monday) as day names.

    APScheduler 3 numbers weekdays from Monday = 0, so "1-5" would fire Tuesday to Saturday.
    Numbers, ranges and steps are expanded to names here; names and "*" pass through."""
    parts = []
    for part in field.split(","):
        body, _, step_text = part.partition("/")
        if not any(ch.isdigit() for ch in body) and not (body == "*" and step_text):
            parts.append(part)
            continue
        step = int(step_text) if step_text else 1
        if body == "*":
            start, end = 0, 6
        elif "-" in body:
            a, b = body.split("-", 1)
            start, end = int(a), int(b)
        else:
            start = int(body)
            end = 6 if step_text else start
        if not (0 <= start <= 7 and 0 <= end <= 7 and step >= 1):
            raise ValueError(f"weekday out of range in {field!r}")
        if end < start:
            raise ValueError(f"descending weekday range in {field!r}")
        parts += [_DAY_NAMES[d % 7] for d in range(start, end + 1, step)]
    return ",".join(dict.fromkeys(parts))


def cron_trigger(expression: str, tz: ZoneInfo) -> CronTrigger:
    fields = expression.split()
    if len(fields) != 5:
        raise ValueError(f"expected 5 fields, got {len(fields)}")
    minute, hour, day, month, weekday = fields
    return CronTrigger(
        minute=minute,
        hour=hour,
        day=day,
        month=month,
        day_of_week=crontab_weekdays(weekday),
        timezone=tz,
    )


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


class ReferenceSettings(ConfigModel):
    """Identity and reference data: OpenFIGI, ESMA FIRDS/FITRS, GLEIF, Wikidata."""

    openfigi_refresh_days: int = Field(
        default=30, ge=1, le=365, description="Re-map each ISIN through OpenFIGI this often."
    )
    wikidata_enabled: bool = Field(
        default=True, description="Read aliases and suggested relationships from Wikidata."
    )
    wikidata_refresh_days: int = Field(default=30, ge=1, le=365)
    max_subsidiary_aliases: int = Field(
        default=50,
        ge=0,
        le=500,
        description="Most GLEIF subsidiary names added to a holding's news aliases.",
    )


StooqSymbol = Annotated[str, Field(pattern=r"^\^?[a-z0-9._-]{1,24}$", examples=["^spx"])]


class PriceSettings(ConfigModel):
    """Daily price history (Stooq) and the risk figures computed from it."""

    benchmark_symbols: list[StooqSymbol] = Field(
        default_factory=lambda: ["^spx"],
        description="Stooq symbols of the benchmarks (^spx S&P 500, ^dax DAX, ^ukx FTSE 100). "
        "The first is used for beta and the holding charts.",
    )
    history_years: int = Field(default=2, ge=1, le=10)
    plausibility_tolerance_pct: float = Field(
        default=1.0,
        gt=0,
        le=20,
        description="Largest gap between Stooq's close and Trading212's price on a sync day "
        "before it is flagged.",
    )


CountryCode = Annotated[str, Field(pattern=r"^[A-Z]{2}$", examples=["CZ"])]
Mic = Annotated[str, Field(pattern=r"^[A-Z0-9]{4}$", examples=["XNYS"])]


class ExchangeClosure(ConfigModel):
    mic: Mic
    date: dt.date
    name: str = Field(min_length=1, max_length=100)


class CalendarSettings(ConfigModel):
    """The expected-gap calendar: days when a source has nothing to publish or collect."""

    holiday_countries: list[CountryCode] = Field(
        default_factory=lambda: ["CZ", "US", "DE", "GB"],
        description="Countries whose public holidays are loaded from Nager.Date.",
    )
    exchange_closures: list[ExchangeClosure] = Field(
        default_factory=list,
        description="Days an exchange is closed (public holidays are not exchange calendars; "
        "confirm the suggestions on the calendar page). Used without a deploy.",
    )

    def closures(self, mic: str) -> set[dt.date]:
        return {c.date for c in self.exchange_closures if c.mic == mic}


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
    reference_data: JobSchedule = Field(
        default_factory=lambda: JobSchedule(cron="30 4 * * 1"),
        description="Identity, listings, legal entities and group structure. Default: Mondays "
        "04:30.",
    )
    price_history: JobSchedule = Field(
        default_factory=lambda: JobSchedule(cron="15 23 * * 1-5"),
        description="Daily prices (Stooq) and FX history after the close. Default: weekdays 23:15.",
    )
    holiday_refresh: JobSchedule = Field(
        default_factory=lambda: JobSchedule(cron="0 4 2 1,7 *"),
        description="Public holidays for this and next year. Default: 2 January and 2 July.",
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
    reference: ReferenceSettings = Field(default_factory=ReferenceSettings)
    prices: PriceSettings = Field(default_factory=PriceSettings)
    calendar: CalendarSettings = Field(default_factory=CalendarSettings)

    def schedules(self) -> dict[str, JobSchedule]:
        return {
            "portfolio_sync": self.portfolio_sync,
            "earnings_calendar": self.earnings_calendar,
            "earnings_calendar_full": self.earnings_calendar_full,
            "earnings_due": self.earnings_due,
            "fact_reconcile": self.fact_reconcile,
            "reference_data": self.reference_data,
            "price_history": self.price_history,
            "holiday_refresh": self.holiday_refresh,
            "news_ingest": self.news_ingest,
            "daily_digest": self.daily_digest,
            "weekly_digest": self.weekly_digest,
        }
