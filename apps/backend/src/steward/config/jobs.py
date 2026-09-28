"""Jobs config: when each scheduled job runs, and whether it runs at all."""

import datetime as dt
from typing import Annotated, Any, Literal, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apscheduler.triggers.cron import CronTrigger
from pydantic import Field, field_validator, model_validator

from steward.config.common import ConfigModel, require_unique


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

Effort = Literal["low", "medium", "high", "xhigh", "max"]
EFFORT_HELP = (
    "Unset: effort is not sent and the model's default applies. On Haiku 4 models the level "
    "sets an extended-thinking budget instead (low 2k to max 32k tokens)."
)
# Web-search tiers: 1 the company, regulators and statistics offices; 2 established outlets;
# 3 everything else on the allow-list.
SourceTier = Literal[1, 2, 3]
DEFAULT_SOURCE_TIERS: tuple[SourceTier, ...] = (1, 2)


def supports_effort(model: str) -> bool:
    """Haiku 4 models reject the effort parameter."""
    return not model.startswith("claude-haiku-4")


# Claude Haiku 4 models take no effort parameter; their equivalent is extended thinking with
# a token budget, sized per effort level.
THINKING_BUDGET: dict[str, int] = {
    "low": 2048,
    "medium": 4096,
    "high": 8192,
    "xhigh": 16384,
    "max": 32000,
}


def reasoning_kwargs(
    model: str,
    effort: str | None,
    max_tokens: int,
    output_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """`max_tokens`, `output_config` and `thinking` for a Messages call at `effort`.

    Models that take effort get it in `output_config` (merged with `output_config`, e.g. a
    response format). Haiku 4 models get extended thinking with `THINKING_BUDGET[effort]`
    tokens instead, and `max_tokens` grows by that budget so the answer keeps its room.
    No effort: nothing is added (the model's default)."""
    config = dict(output_config or {})
    out: dict[str, Any] = {"max_tokens": max_tokens}
    if effort is not None:
        if supports_effort(model):
            # xhigh arrived with Opus 4.7; the 4.6 models top out at high below max.
            if effort == "xhigh" and model.endswith("-4-6"):
                effort = "high"
            config["effort"] = effort
        else:
            budget = THINKING_BUDGET[effort]
            out["thinking"] = {"type": "enabled", "budget_tokens": budget}
            out["max_tokens"] = max_tokens + budget
    if config:
        out["output_config"] = config
    return out


def effort_kwargs(model: str, effort: str | None, max_tokens: int) -> dict[str, Any]:
    """`reasoning_kwargs` without a response format."""
    return reasoning_kwargs(model, effort, max_tokens)


def check_effort(model: str, effort: str | None, field: str) -> None:
    """Every effort level is valid for every model (Haiku 4 maps it to a thinking budget)."""
    del model, effort, field


def _unique_tiers(tiers: list[int]) -> list[int]:
    if not tiers:
        raise ValueError("at least one source tier is needed")
    return sorted(set(tiers))


class EarningsCheckSettings(ConfigModel):
    """The earnings thesis check: find the release, extract figures, validate, judge KPIs."""

    extraction_model: ModelId = "claude-sonnet-5"
    locate_model: ModelId = Field(
        default="claude-sonnet-5",
        description="Searches for the release when EDGAR has none (non-US issuers).",
    )
    effort: Effort = "high"
    locate_effort: Effort | None = Field(default=None, description=EFFORT_HELP)
    max_steps: int = Field(
        default=40, ge=2, le=200, description="Claude calls plus document fetches per run."
    )
    max_searches: int = Field(default=20, ge=0, le=100, description="Web searches per run.")
    check_days_after_report: list[Annotated[int, Field(ge=0, le=30)]] = Field(
        default_factory=lambda: [1, 2, 4],
        description="Days after the report date on which the check runs until it finds the "
        "release (the spec's +1, retry at +2 and +4).",
    )
    max_document_mb: int = Field(default=20, ge=1, le=32)
    kpi_history_periods: int = Field(
        default=8,
        ge=1,
        le=40,
        description="Past periods (quarters or years) judged for each KPI's history.",
    )

    @model_validator(mode="after")
    def _effort_supported(self) -> Self:
        check_effort(self.locate_model, self.locate_effort, "locate_effort")
        return self


class EarningsCalendarSettings(ConfigModel):
    """Next report dates: Finnhub for US issuers, the IR calendar page otherwise."""

    model: ModelId = Field(
        default="claude-haiku-4-5", description="Reads report dates off IR calendar pages."
    )
    effort: Effort | None = Field(default=None, description=EFFORT_HELP)
    lookahead_days: int = Field(default=120, ge=7, le=400)
    refresh_within_days: int = Field(
        default=14,
        ge=1,
        le=120,
        description="The weekly run refreshes holdings reporting within this many days (and "
        "those with no date); the quarterly run refreshes every holding.",
    )

    @model_validator(mode="after")
    def _effort_supported(self) -> Self:
        check_effort(self.model, self.effort, "effort")
        return self


class NewsSettings(ConfigModel):
    """The news pipeline after ingest: classification, corroboration."""

    classification_model: ModelId = "claude-haiku-4-5"
    classification_effort: Effort | None = Field(default=None, description=EFFORT_HELP)
    classification_batch_size: int = Field(
        default=50, ge=5, le=100, description="Items per classification call."
    )
    corroboration_model: ModelId = "claude-haiku-4-5"
    corroboration_effort: Effort | None = Field(default=None, description=EFFORT_HELP)
    corroboration_max_searches: int = Field(
        default=10, ge=0, le=50, description="Web searches per corroboration call."
    )
    corroboration_rounds: int = Field(
        default=3,
        ge=0,
        le=20,
        description="Corroboration searches per story: one when it is first seen, the rest "
        "spread over the re-check window.",
    )
    recheck_hours: int = Field(
        default=48, ge=1, le=168, description="How long an unconfirmed story is re-checked."
    )
    max_items_per_run: int = Field(
        default=10_000, ge=10, le=100_000, description="Classification cap per run (cost guard)."
    )
    edgar_fts_max_hits: int = Field(
        default=100,
        ge=10,
        le=10_000,
        description="Most EDGAR full-text search hits read per query (100 per request; EDGAR "
        "serves at most 10,000). A query with more is noted in the collect step.",
    )

    @model_validator(mode="after")
    def _effort_supported(self) -> Self:
        check_effort(self.classification_model, self.classification_effort, "classification_effort")
        check_effort(self.corroboration_model, self.corroboration_effort, "corroboration_effort")
        return self


class DigestSettings(ConfigModel):
    model: ModelId = "claude-sonnet-5"
    effort: Effort | None = Field(default=None, description=EFFORT_HELP)
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
    min_severity_listed: int = Field(
        default=1,
        ge=1,
        le=3,
        description="Stories below this severity are left out of the digest entirely (at most "
        "min_severity_summarised).",
    )
    daily_window_hours: int = Field(
        default=24, ge=1, le=168, description="The daily digest covers this many past hours."
    )
    weekly_window_days: int = Field(
        default=7, ge=1, le=31, description="The weekly digest covers this many past days."
    )

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        check_effort(self.model, self.effort, "effort")
        if self.min_severity_listed > self.min_severity_summarised:
            raise ValueError(
                "min_severity_listed must be at most min_severity_summarised (a summarised "
                "story must also be listed)"
            )
        return self

    def window(self, kind: str) -> dt.timedelta:
        if kind == "daily":
            return dt.timedelta(hours=self.daily_window_hours)
        return dt.timedelta(days=self.weekly_window_days)


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
    wikidata_languages: list[Annotated[str, Field(pattern=r"^[a-z]{2,3}$")]] = Field(
        default_factory=lambda: ["en", "de", "cs"],
        min_length=1,
        max_length=10,
        description="Languages (ISO 639 codes) whose Wikidata labels and aliases are read.",
    )


StooqSymbol = Annotated[str, Field(pattern=r"^\^?[a-z0-9._-]{1,24}$", examples=["^spx"])]


YahooSymbol = Annotated[
    str, Field(pattern=r"^\^?[A-Z0-9][A-Z0-9.=-]{0,23}$", examples=["^GSPC", "VWCE.DE"])
]

# Yahoo symbols of the Stooq indices whose Yahoo counterpart is known (checked on
# 2026-09-28: ^GSPC and ^STOXX answer; ^SXXP has no data, and Yahoo's ^PX is a CBOE symbol,
# not Prague's PX, so PX has none). Used when a market strip item or benchmark names none.
STOOQ_INDEX_YAHOO: dict[str, str] = {
    "^spx": "^GSPC", "^stoxx": "^STOXX", "^dax": "^GDAXI", "^ukx": "^FTSE", "^ndq": "^IXIC",
    "^dji": "^DJI", "^cac": "^FCHI", "^aex": "^AEX", "^nkx": "^N225",
}  # fmt: skip


class MarketStripItem(ConfigModel):
    symbol: StooqSymbol
    label: str = Field(min_length=1, max_length=40, examples=["S&P 500"])
    yahoo_symbol: YahooSymbol | None = Field(
        default=None,
        description="Yahoo Finance symbol of the same index (^GSPC, ^STOXX): intraday values, "
        "and closes when Stooq has none. Empty: the known counterpart of the Stooq symbol.",
    )

    def yahoo(self) -> str | None:
        return self.yahoo_symbol or STOOQ_INDEX_YAHOO.get(self.symbol)


def _default_market_strip() -> list[MarketStripItem]:
    return [
        MarketStripItem(symbol="^spx", label="S&P 500", yahoo_symbol="^GSPC"),
        MarketStripItem(symbol="^stoxx", label="STOXX Europe 600", yahoo_symbol="^STOXX"),
        MarketStripItem(symbol="^px", label="PX"),
    ]


IntradayInterval = Literal[5, 10, 15, 20, 30, 60]


class PriceSettings(ConfigModel):
    """Daily price history (Stooq, Yahoo as the fallback), intraday prices (Yahoo) and the
    risk figures computed from the daily closes."""

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
    market_strip: list[MarketStripItem] = Field(
        default_factory=_default_market_strip,
        max_length=12,
        description="Index quotes on the market strip: Stooq symbols (collected with the "
        "benchmarks), the label shown and optionally the Yahoo symbol of the same index. "
        "Stooq's close first; else Yahoo's; for PX (^px) the Prague Stock Exchange's page, "
        "read once a day. An index no source has data for shows as empty.",
    )
    yahoo_daily_fallback: bool = Field(
        default=True,
        description="Daily closes from Yahoo Finance (unofficial) for a symbol Stooq refuses, "
        "cannot be reached for or has no symbol for. Stooq stays the source of record.",
    )
    intraday_enabled: bool = Field(
        default=True,
        description="Intraday prices (Yahoo Finance, unofficial): 5-minute bars about 15 "
        "minutes delayed, polled only while each listing's market is open, and fetched when a "
        "holding page opens. Unofficial: it may rate-limit or break without notice; pages "
        "then fall back to daily closes with a note.",
    )
    intraday_interval_minutes: IntradayInterval = Field(
        default=15,
        description="How often intraday prices are polled while a market is open (minutes).",
    )
    intraday_retention_days: int = Field(
        default=5, ge=1, le=14, description="Days of 5-minute bars kept."
    )
    intraday_cache_seconds: int = Field(
        default=120,
        ge=30,
        le=900,
        description="A holding page re-fetches its intraday prices when the stored ones are "
        "older than this during trading hours.",
    )

    @model_validator(mode="after")
    def _unique_strip(self) -> Self:
        require_unique("market strip symbols", (i.symbol for i in self.market_strip))
        return self

    def index_yahoo(self, symbol: str) -> str | None:
        """The Yahoo symbol of a Stooq index symbol (a strip item's own, else the known one)."""
        item = next((i for i in self.market_strip if i.symbol == symbol), None)
        return item.yahoo() if item else STOOQ_INDEX_YAHOO.get(symbol)


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


class EscalationSettings(ConfigModel):
    """Event escalation: an impact note on a confirmed severity-2+ story or a price move."""

    enabled: bool = True
    model: ModelId = "claude-sonnet-5"
    effort: Effort | None = Field(default=None, description=EFFORT_HELP)
    max_steps: int = Field(
        default=30, ge=1, le=100, description="Claude calls plus web searches per escalation."
    )
    max_searches: int = Field(default=15, ge=0, le=50)
    min_severity: int = Field(default=2, ge=1, le=3)
    source_tiers: list[SourceTier] = Field(
        default_factory=lambda: list(DEFAULT_SOURCE_TIERS),
        min_length=1,
        description="Outlet tiers web search may use (News sources config).",
    )
    price_move_news_days: int = Field(
        default=3,
        ge=0,
        le=30,
        description="A price-move note reads the holding's news from this many days before "
        "the move.",
    )
    price_move_news_items: int = Field(default=6, ge=0, le=30)

    _tiers = field_validator("source_tiers")(_unique_tiers)

    @model_validator(mode="after")
    def _effort_supported(self) -> Self:
        check_effort(self.model, self.effort, "effort")
        return self


class EurostatSeries(ConfigModel):
    key: Annotated[str, Field(pattern=r"^[a-z0-9_]{1,40}$")]
    label: str = Field(min_length=1, max_length=100)
    dataset: str = Field(pattern=r"^[a-z0-9_]{3,40}$")
    filters: dict[str, str] = Field(
        default_factory=dict, description="Dimension filters, e.g. {'unit': 'RCH_A'}."
    )
    unit: str = Field(default="%", max_length=20)


class FredSeries(ConfigModel):
    key: Annotated[str, Field(pattern=r"^[a-z0-9_]{1,40}$")]
    label: str = Field(min_length=1, max_length=100)
    series_id: str = Field(pattern=r"^[A-Z0-9_]{1,30}$")
    units: Literal["lin", "pch", "pc1", "chg"] = "lin"
    unit: str = Field(default="%", max_length=20)
    policy_rate: bool = False


class EcbSeries(ConfigModel):
    key: Annotated[str, Field(pattern=r"^[a-z0-9_]{1,40}$")]
    label: str = Field(min_length=1, max_length=100)
    flow_key: str = Field(
        pattern=r"^[A-Z0-9_]+/[A-Za-z0-9._+]+$",
        description="Data Portal flow/key, e.g. FM/D.U2.EUR.4F.KR.DFR.LEV.",
    )
    unit: str = Field(default="%", max_length=20)
    policy_rate: bool = False


def _eurostat_defaults() -> list[EurostatSeries]:
    rows = [
        ("hicp", "HICP inflation, annual rate", "prc_hicp_minr",
         {"unit": "RCH_A", "coicop18": "TOTAL"}, "%"),
        ("unemployment", "Unemployment rate", "une_rt_m",
         {"s_adj": "SA", "age": "TOTAL", "sex": "T", "unit": "PC_ACT"}, "%"),
        ("gdp", "GDP, change on previous quarter", "namq_10_gdp",
         {"unit": "CLV_PCH_PRE", "s_adj": "SCA", "na_item": "B1GQ"}, "%"),
        ("industrial_production", "Industrial production, change on previous month",
         "sts_inpr_m", {"indic_bt": "PRD", "nace_r2": "B-D", "s_adj": "SCA", "unit": "PCH_PRE"},
         "%"),
        ("sentiment", "Economic sentiment indicator", "ei_bssi_m_r2",
         {"indic": "BS-ESI-I", "s_adj": "SA"}, "index"),
    ]  # fmt: skip
    return [
        EurostatSeries(key=k, label=label, dataset=d, filters=f, unit=u)
        for k, label, d, f, u in rows
    ]


class MacroSettings(ConfigModel):
    """Macro series collected into `macro_series` (numbers in digests come only from here)."""

    history_years: int = Field(
        default=10,
        ge=1,
        le=30,
        description="Years of history loaded the first time a series is collected (later runs "
        "fetch only the recent observations, and fill any gap since the last stored one).",
    )

    geos: list[Annotated[str, Field(pattern=r"^[A-Z0-9]{2,5}$")]] = Field(
        default_factory=lambda: ["CZ", "DE", "EA21"],
        description="Eurostat areas: countries and the euro area (EA21 from 2026).",
    )
    eurostat: list[EurostatSeries] = Field(default_factory=_eurostat_defaults)
    fred: list[FredSeries] = Field(
        default_factory=lambda: [
            FredSeries(
                key="us_cpi",
                label="US CPI inflation, annual rate",
                series_id="CPIAUCSL",
                units="pc1",
            ),
            FredSeries(key="us_unemployment", label="US unemployment rate", series_id="UNRATE"),
            FredSeries(
                key="fed_funds", label="Fed funds effective rate", series_id="DFF", policy_rate=True
            ),
        ]
    )
    ecb: list[EcbSeries] = Field(
        default_factory=lambda: [
            EcbSeries(
                key="ecb_deposit_rate",
                label="ECB deposit facility rate",
                flow_key="FM/D.U2.EUR.4F.KR.DFR.LEV",
                policy_rate=True,
            ),
        ]
    )


class MonitorSettings(ConfigModel):
    """Monitoring: failed jobs, missed scheduled runs and per-holding coverage gaps alert."""

    alert_failed_jobs: list[str] = Field(
        default_factory=lambda: [
            "portfolio_sync",
            "news_ingest",
            "earnings_check",
            "daily_digest",
            "weekly_digest",
            "reference_data",
            "price_history",
            "macro_collect",
        ],
        description="Job types whose failed runs alert (once per run).",
    )
    missed_run_grace_minutes: int = Field(
        default=90,
        ge=5,
        le=1440,
        description="A scheduled run that has not succeeded this long after its slot is missed.",
    )
    news_coverage_hours: int = Field(
        default=48,
        ge=6,
        le=336,
        description="A holding with no successful news run this long raises a coverage alert.",
    )


class ChatSettings(ConfigModel):
    """The chat panel's assistant: DB tools, web search on tiered outlets, cited answers."""

    model: ModelId = "claude-sonnet-5"
    effort: Effort | None = Field(default=None, description=EFFORT_HELP)
    max_steps: int = Field(
        default=30, ge=1, le=100, description="Claude calls plus tool rounds per answer."
    )
    max_searches: int = Field(default=15, ge=0, le=50, description="Web searches per answer.")
    history_messages: int = Field(
        default=100, ge=2, le=500, description="Earlier messages of the conversation sent along."
    )
    source_tiers: list[SourceTier] = Field(
        default_factory=lambda: list(DEFAULT_SOURCE_TIERS),
        min_length=1,
        description="Outlet tiers web search may use and an answer may cite (News sources config).",
    )

    _tiers = field_validator("source_tiers")(_unique_tiers)

    @model_validator(mode="after")
    def _effort_supported(self) -> Self:
        check_effort(self.model, self.effort, "effort")
        return self


class ReviewSettings(ConfigModel):
    """The monthly review: compliance and figures in code, proposed actions by the model."""

    model: ModelId = "claude-opus-5-5"
    effort: Effort = "high"
    max_steps: int = Field(default=10, ge=1, le=50, description="Claude calls per review.")
    use_batch: bool = Field(
        default=True, description="Propose the actions through the Batch API (half price)."
    )
    batch_wait_minutes: int = Field(
        default=60,
        ge=1,
        le=720,
        description="How long to wait for the batch before proposing the actions directly.",
    )
    liquidity_days: float = Field(
        default=1.0,
        gt=0,
        le=100,
        description="A position larger than this many days of its home market's average "
        "daily turnover is flagged as hard to exit.",
    )
    holder_drop_flag_pct: float = Field(
        default=10.0,
        gt=0,
        le=100,
        description="A fall in a holding's 13F holder count of at least this percentage "
        "quarter on quarter is flagged.",
    )


class TrackedManager(ConfigModel):
    cik: Annotated[str, Field(pattern=r"^[0-9]{1,10}$")]
    name: str = Field(min_length=1, max_length=200)


class OwnershipSettings(ConfigModel):
    """Form 13F institutional ownership from the SEC's quarterly data sets."""

    enabled: bool = True
    index_url: str = Field(
        default="https://www.sec.gov/data-research/sec-markets-data/form-13f-data-sets",
        description="The SEC page listing the data sets (zip files).",
    )
    datasets_per_run: int = Field(
        default=2, ge=1, le=8, description="Data sets (about 100 MB each) loaded per run."
    )
    history_datasets: int = Field(
        default=8,
        ge=1,
        le=60,
        description="How far back to load: the newest this many data sets (one per quarter, "
        "so 8 is two years). An empty database is backfilled over successive runs, "
        "datasets_per_run at a time; the datasets step shows how many remain.",
    )
    tracked_managers: list[TrackedManager] = Field(
        default_factory=list,
        description="Managers whose new and exited positions in the portfolio's holdings "
        "appear in the next digest.",
    )
    top_holders: int = Field(default=10, ge=1, le=50)


class JobsConfig(ConfigModel):
    """Schedules for the worker's timed jobs. Stored as `config_version` rows, domain `jobs`.

    The worker re-reads this every few minutes, so a saved change applies without a restart.
    """

    schema_version: Literal[1] = 1
    catch_up_on_start: bool = Field(
        default=True,
        description="On worker start, run the data jobs that never succeeded or missed their "
        "last scheduled run, one at a time in dependency order.",
    )
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
        description="Public holidays for last, this and next year. Default: 2 January and 2 July.",
    )
    macro_collect: JobSchedule = Field(
        default_factory=lambda: JobSchedule(cron="30 11 * * *"),
        description="FRED, ECB and Eurostat macro series; macro surprises on release days. "
        "Default: daily 11:30 (after Eurostat's 11:00 releases).",
    )
    monitor: JobSchedule = Field(
        default_factory=lambda: JobSchedule(cron="*/15 * * * *"),
        description="Alert on failed jobs, missed runs and coverage gaps. Default: every 15 "
        "minutes.",
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
    monthly_review: JobSchedule = Field(
        default_factory=lambda: JobSchedule(cron="0 7 1-3 * *"),
        description="Monthly review on the first weekday of the month (a scheduled run on "
        "another day does nothing). Default: 07:00 on the 1st-3rd.",
    )
    ownership_13f: JobSchedule = Field(
        default_factory=lambda: JobSchedule(cron="0 5 5 * *"),
        description="Load new SEC Form 13F data sets (published quarterly). Default: the 5th "
        "of each month, 05:00.",
    )
    earnings_check: EarningsCheckSettings = Field(default_factory=EarningsCheckSettings)
    earnings_calendar_settings: EarningsCalendarSettings = Field(
        default_factory=EarningsCalendarSettings
    )
    news: NewsSettings = Field(default_factory=NewsSettings)
    digest: DigestSettings = Field(default_factory=DigestSettings)
    escalation: EscalationSettings = Field(default_factory=EscalationSettings)
    macro: MacroSettings = Field(default_factory=MacroSettings)
    monitoring: MonitorSettings = Field(default_factory=MonitorSettings)
    reference: ReferenceSettings = Field(default_factory=ReferenceSettings)
    prices: PriceSettings = Field(default_factory=PriceSettings)
    calendar: CalendarSettings = Field(default_factory=CalendarSettings)
    chat: ChatSettings = Field(default_factory=ChatSettings)
    review: ReviewSettings = Field(default_factory=ReviewSettings)
    ownership: OwnershipSettings = Field(default_factory=OwnershipSettings)

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
            "macro_collect": self.macro_collect,
            "monitor": self.monitor,
            "news_ingest": self.news_ingest,
            "daily_digest": self.daily_digest,
            "weekly_digest": self.weekly_digest,
            "monthly_review": self.monthly_review,
            "ownership_13f": self.ownership_13f,
        }

    def interval_schedules(self) -> dict[str, JobSchedule]:
        """Jobs whose schedule follows from a setting rather than a cron of their own: the
        intraday poll (Prices: enabled and interval), weekdays in the hours any of the
        supported markets can be open (UTC; each listing's own session is checked per run).
        Not watched for missed runs: a skipped poll is covered by the next one."""
        prices = self.prices
        return {
            "intraday_prices": JobSchedule(
                enabled=prices.intraday_enabled,
                cron=f"*/{prices.intraday_interval_minutes} 6-21 * * 1-5"
                if prices.intraday_interval_minutes < 60
                else "0 6-21 * * 1-5",
                timezone="UTC",
            )
        }

    def worker_schedules(self) -> dict[str, JobSchedule]:
        """Everything the worker schedules: the cron schedules and the interval ones."""
        return {**self.schedules(), **self.interval_schedules()}

    def next_run_after(self, job_type: str, moment: dt.datetime) -> dt.datetime | None:
        """The first scheduled run of `job_type` after `moment`; None when the job has no
        schedule or it is disabled. Data a run produced is due for renewal at this time,
        so a Friday digest is not stale on Saturday when digests run on weekdays only."""
        schedule = self.schedules().get(job_type)
        if schedule is None or not schedule.enabled:
            return None
        # Strictly after: a run created on its own slot is not due again at that slot.
        fire: dt.datetime | None = schedule.trigger().get_next_fire_time(
            None, moment + dt.timedelta(seconds=1)
        )
        return fire
