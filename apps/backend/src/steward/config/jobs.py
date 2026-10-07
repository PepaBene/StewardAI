"""Jobs config: when each scheduled job runs, and whether it runs at all."""

import datetime as dt
from typing import Annotated, Any, Literal, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apscheduler.triggers.cron import CronTrigger
from pydantic import AnyHttpUrl, Field, field_validator, model_validator

from steward.config.common import ConfigModel, Ticker, require_unique
from steward.config.holding_profiles import Domain
from steward.macro.commodity_series import COMMODITY_FRED


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


ModelId = Annotated[
    str,
    Field(
        pattern=r"^(claude|gpt|gemini)-[a-z0-9.-]{1,64}$",
        examples=["claude-sonnet-5", "gpt-6-luna"],
    ),
]

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
    xbrl_for_filings: bool = Field(
        default=True,
        description="When the document is a 10-Q or 10-K already on EDGAR, take the standard"
        " figures from its XBRL (each still validated against the document text) and call the"
        " model only for the rest. Not measured against a golden set yet.",
    )
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
    use_batch: bool = Field(
        default=True,
        description="Read the IR pages in one provider batch (about half the price, results "
        "within hours). Only for OpenAI and Google models.",
    )
    batch_wait_minutes: int = Field(default=60, ge=1, le=1440)

    @model_validator(mode="after")
    def _effort_supported(self) -> Self:
        check_effort(self.model, self.effort, "effort")
        return self


class AiApprovalSettings(ConfigModel):
    """AI approval of suggested listings and peers. Nothing waits for a click; every decision
    is recorded with its reasons and the owner can overturn it (an owner decision is final)."""

    model: ModelId = Field(
        default="claude-sonnet-5", description="Judges each suggestion from the evidence given."
    )
    effort: Effort | None = Field(default=None, description=EFFORT_HELP)
    listings: bool = Field(default=True, description="Approve suggested listing matches.")
    peers: bool = Field(default=True, description="Approve or reject suggested peers.")
    refresh_peers: bool = Field(
        default=True,
        description="Fetch Finnhub peer candidates and EPS estimates for holdings that have "
        "none or whose are older than `peer_refresh_days`.",
    )
    peer_refresh_days: int = Field(default=30, ge=1, le=365)
    peer_refresh_per_run: int = Field(default=8, ge=1, le=40)
    min_confidence: float = Field(
        default=0.8,
        ge=0.5,
        le=1.0,
        description="Below this the suggestion is left as it is, for the next run or the owner.",
    )
    max_confirmed_peers: int = Field(default=8, ge=1, le=20)
    max_calls_per_run: int = Field(default=40, ge=1, le=200)
    use_sic: bool = Field(
        default=True,
        description="Look up each peer candidate's SEC industry code (SIC). A candidate with "
        "the holding's four-digit code that the owner also names as a competitor is confirmed "
        "in code; the model sees every code as extra evidence and decides the rest.",
    )
    rules_first: bool = Field(
        default=True,
        description="Settle a listing in code when the market suffix, ISIN country and symbol "
        "all agree (confirm) or the suffix and symbol both fail (reject); the AI only judges "
        "the rest. Each decision records whether a rule or the model made it.",
    )
    use_batch: bool = Field(
        default=True,
        description="Judge the suggestions side by side and send them as one provider batch, "
        "about half the price. Only for OpenAI and Google models.",
    )
    batch_wait_minutes: int = Field(default=60, ge=1, le=1440)

    @model_validator(mode="after")
    def _effort_supported(self) -> Self:
        check_effort(self.model, self.effort, "effort")
        return self


class ExposureBuildSettings(ConfigModel):
    """The AI steps of the exposure builder. The country split from XBRL needs no AI and always
    runs. Each annual report is read at most once; nothing is guessed: a figure is stored only
    when a verbatim quote in the filing prints it."""

    model: ModelId = Field(
        default="claude-sonnet-5", description="Reads annual reports for currencies and inputs."
    )
    effort: Effort | None = Field(default=None, description=EFFORT_HELP)
    filings_per_run: int = Field(
        default=6, ge=1, le=40, description="Annual reports read per run (cost guard)."
    )
    extract_inputs: bool = Field(
        default=True, description="Read currency and commodity inputs from the annual report."
    )
    extract_dependencies: bool = Field(
        default=True,
        description="Read named customers and suppliers from the annual report; the AI approves "
        "them and the owner can overturn.",
    )
    map_products: bool = Field(
        default=True,
        description="Map reported product lines to HS trade codes (always marked estimated).",
    )


class IrDiscoverySettings(ConfigModel):
    """Finding each holding's investor-relations pages by web search, checking them, and having
    AI approve them. Pages the owner already set are never changed."""

    model: ModelId = Field(
        default="claude-sonnet-5", description="Searches for the pages and approves them."
    )
    effort: Effort | None = Field(default=None, description=EFFORT_HELP)
    max_searches: int = Field(
        default=4, ge=1, le=10, description="Web searches per holding, the expensive part."
    )
    holdings_per_run: int = Field(default=6, ge=1, le=40)
    use_batch: bool = Field(
        default=True,
        description="Run the holdings side by side and send each round of calls (all the "
        "searches, then all the judgements) as one provider batch, about half the price. Only "
        "for OpenAI and Google models; the run waits for the batch.",
    )
    batch_wait_minutes: int = Field(default=60, ge=1, le=1440)
    probe_first: bool = Field(
        default=True,
        description="Before the web search, try common investor-relations paths on the "
        "domains the holding profile already names; search only when none loads a page "
        "naming the company.",
    )
    browser_fallback: bool = Field(
        default=False,
        description="Retry a page in a headless browser when a plain request is refused "
        "(HTTP 403/429) or returns an empty JavaScript shell. Still obeys robots.txt and never "
        "logs in or gets past a challenge. Needs the browser build of the image.",
    )
    cooldown_days: int = Field(
        default=30, ge=1, le=365, description="Days before a holding without a result is retried."
    )
    min_confidence: float = Field(
        default=0.8,
        ge=0.5,
        le=1.0,
        description="Below this a page is not applied (it stays in the decision log).",
    )
    denied_domains: list[Domain] = Field(
        default_factory=lambda: [
            "wikipedia.org",
            "linkedin.com",
            "facebook.com",
            "x.com",
            "twitter.com",
            "youtube.com",
            "reddit.com",
            "seekingalpha.com",
            "finance.yahoo.com",
            "marketwatch.com",
            "investing.com",
            "stocktwits.com",
        ],
        description="Domains never accepted as an investor page.",
    )
    robots_exempt_domains: list[Domain] = Field(
        default_factory=list,
        description="Investor sites whose robots.txt is not applied when their pages are "
        "read for discovery. Your decision per site: requests still go to public addresses "
        "only, are spaced out, state who is asking, never log in and never get past a "
        "challenge. Leave empty to obey robots.txt everywhere.",
    )

    @model_validator(mode="after")
    def _effort_supported(self) -> Self:
        check_effort(self.model, self.effort, "effort")
        return self


GapSlotKey = Literal[
    "next_report_date",
    "company_description",
    "shares_outstanding",
    "annual_revenue",
    "segment_revenue",
    "analyst_estimates",
]


def _default_gap_slots() -> list[GapSlotKey]:
    return ["next_report_date", "shares_outstanding", "company_description"]


class GapResearchSettings(ConfigModel):
    """Filling what the structured sources left empty from reliable web pages, last resort.
    Only empty, fillable slots off their cooldown are searched. Facts are kept apart from the
    structured data and are approved by AI against the source tiers below."""

    model: ModelId = Field(
        default="claude-sonnet-5", description="Searches for facts and approves them."
    )
    effort: Effort | None = Field(default=None, description=EFFORT_HELP)
    slots: list[GapSlotKey] = Field(
        default_factory=_default_gap_slots,
        description="Which gaps to research. `annual_revenue` is off by default: enable it to "
        "fill the latest fiscal-year revenue of holdings with no filing data. "
        "`analyst_estimates` is recorded as unavailable (a paid vendor product) and is never "
        "searched.",
    )
    max_searches: int = Field(default=3, ge=1, le=10, description="Web searches per slot.")
    slots_per_run: int = Field(default=8, ge=1, le=60, description="Cost guard per run.")
    use_batch: bool = Field(
        default=True,
        description="Run the slots side by side and send each round of calls as one provider "
        "batch, about half the price. Only for OpenAI and Google models.",
    )
    batch_wait_minutes: int = Field(default=60, ge=1, le=1440)
    browser_fallback: bool = Field(
        default=False,
        description="Retry a page in a headless browser when a plain request is refused "
        "(HTTP 403/429) or returns an empty JavaScript shell. Still obeys robots.txt and never "
        "logs in or gets past a challenge. Needs the browser build of the image.",
    )
    cooldown_days: int = Field(
        default=30, ge=1, le=365, description="Days before an unfilled slot is searched again."
    )
    min_confidence: float = Field(
        default=0.8,
        ge=0.5,
        le=1.0,
        description="Below this a fact is stored for review, not approved.",
    )
    t1_domains: list[Domain] = Field(
        default_factory=lambda: [
            "sec.gov",
            "edinet-fsa.go.jp",
            "esma.europa.eu",
            "londonstockexchange.com",
            "jpx.co.jp",
            "nasdaq.com",
            "nyse.com",
            "euronext.com",
            "deutsche-boerse.com",
            "pse.cz",
            "ecb.europa.eu",
            "federalreserve.gov",
        ],
        description="Regulators, registers and exchanges: accepted on their own. The company's "
        "own domains are always tier 1.",
    )
    t2_domains: list[Domain] = Field(
        default_factory=lambda: [
            "reuters.com",
            "bloomberg.com",
            "ft.com",
            "wsj.com",
            "barrons.com",
            "morningstar.com",
            "marketscreener.com",
            "globenewswire.com",
            "prnewswire.com",
            "businesswire.com",
        ],
        description="Established press and wire services: dates are accepted alone, numbers "
        "need a second agreeing source.",
    )
    t3_domains: list[Domain] = Field(
        default_factory=lambda: ["wikipedia.org", "wikidata.org"],
        description="Reference sites: names and descriptions only, never a figure.",
    )
    denied_domains: list[Domain] = Field(
        default_factory=lambda: [
            "linkedin.com",
            "facebook.com",
            "x.com",
            "twitter.com",
            "youtube.com",
            "reddit.com",
            "stocktwits.com",
            "seekingalpha.com",
            "fool.com",
            "investing.com",
        ],
        description="Never used. Domains in no list are treated as rejected.",
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
    classification_use_batch: bool = Field(
        default=True,
        description="Send a run's classification calls through the provider's batch API (half"
        " price, answers within minutes to hours) when the model is an OpenAI or Google one."
        " A group the batch does not answer is classified directly. Off: every call is direct,"
        " so a severe item is labelled at once.",
    )
    classification_batch_wait_minutes: float = Field(
        default=30, ge=1, le=1440, description="How long a classification batch is waited for."
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


class CnbSeries(ConfigModel):
    """A Czech National Bank rate from its keyless text files."""

    key: Annotated[str, Field(pattern=r"^[a-z0-9_]{1,40}$")]
    label: str = Field(min_length=1, max_length=100)
    series: Literal["czeonia", "repo_2w"] = Field(
        description="czeonia: the CZK overnight reference rate, daily (yearly.txt/daily.txt; "
        "the portfolio's risk-free rate). repo_2w: the 2-week repo rate's change history."
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
            *(
                FredSeries(key=key, label=label, series_id=series_id, unit=unit)
                for key, label, series_id, unit, _words in COMMODITY_FRED
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
            EcbSeries(
                key="euro_area_private_credit",
                label="Euro-area MFI loans to euro-area residents",
                flow_key="BSI/M.U2.N.A.A20.A.1.U2.2300.Z01.E",
                unit="EUR millions",
            ),
            EcbSeries(
                key="euro_area_10y_yield",
                label="Euro-area central government 10-year spot rate",
                flow_key="YC/B.U2.EUR.4F.G_N_A.SV_C_YM.SR_10Y",
                unit="%",
            ),
            EcbSeries(
                key="euro_area_m3_growth",
                label="Euro-area M3 annual growth rate",
                flow_key="BSI/BSI.M.U2.Y.V.M30.A.I.U2.0000.Z01.A",
                unit="%",
            ),
            EcbSeries(
                key="euro_area_household_loan_rate",
                label="Euro-area cost of borrowing for household house purchase",
                flow_key="MIR/MIR.M.U2.B.A2C.AM.R.A.2250.EUR.N",
                unit="% p.a.",
            ),
            EcbSeries(
                key="euro_area_corporate_loan_rate",
                label="Euro-area cost of borrowing for corporations",
                flow_key="MIR/M.U2.B.A2I.AM.R.A.2240.EUR.N",
                unit="% p.a.",
            ),
            EcbSeries(
                key="ea_household_overnight_deposit_rate",
                label="Euro-area overnight deposit rate for households",
                flow_key="MIR/M.U2.B.L21.A.R.A.2250.EUR.N",
                unit="% p.a.",
            ),
            EcbSeries(
                key="ea_corporate_term_deposit_rate",
                label="Euro-area agreed-maturity deposit rate for corporations",
                flow_key="MIR/M.U2.B.L22.A.R.A.2240.EUR.N",
                unit="% p.a.",
            ),
            EcbSeries(
                key="ea_bls_enterprise_credit_standards",
                label="Euro-area bank lending survey: enterprise credit standards",
                flow_key="BLS/BLS.Q.U2.ALL.O.E.Z.B3.ST.S.WFNET",
                unit="net % balance",
            ),
        ]
    )
    cnb: list[CnbSeries] = Field(
        default_factory=lambda: [
            # Moves every business day: not a policy rate, and no surprise band.
            CnbSeries(key="czeonia", label="CZEONIA (CZK overnight rate)", series="czeonia"),
            CnbSeries(
                key="cnb_repo_2w", label="ČNB 2-week repo rate", series="repo_2w", policy_rate=True
            ),
        ],
        description="ČNB rates. CZEONIA is the risk-free rate of the Sharpe ratio (series key "
        "'czeonia'); the repo rate's changes are ČNB decisions.",
    )


class ProviderJobSettings(ConfigModel):
    """Limits for a free provider. There is no on/off switch: a provider is used as soon as it is
    available, meaning its API key is set when it needs one."""

    @model_validator(mode="before")
    @classmethod
    def _drop_legacy_enabled(cls, data: Any) -> Any:
        # Older saved configs carried a per-provider `enabled` switch; it no longer exists.
        if isinstance(data, dict) and "enabled" in data:
            return {k: v for k, v in data.items() if k != "enabled"}
        return data

    refresh_hours: int = Field(default=168, ge=1, le=8760)
    daily_quota: int | None = Field(default=None, ge=1)
    monthly_quota: int | None = Field(default=None, ge=1)
    reserve_credits: int = Field(default=1, ge=0)


class WorldBankIndicator(ConfigModel):
    key: str = Field(pattern=r"^[A-Z0-9.]{3,40}$")
    label: str = Field(min_length=1, max_length=120)
    unit: str = Field(min_length=1, max_length=40)
    consumer: str = Field(min_length=1, max_length=160)


class PublicSdmxSeries(ConfigModel):
    provider: Literal["oecd", "bis"]
    key: str = Field(pattern=r"^[a-z0-9_]{1,60}$")
    label: str = Field(min_length=1, max_length=120)
    base_url: AnyHttpUrl
    flow: str = Field(min_length=1, max_length=160)
    series_key: str = Field(min_length=1, max_length=300)
    geography: str = Field(min_length=2, max_length=12)
    unit: str = Field(min_length=1, max_length=40)
    frequency: str = Field(min_length=1, max_length=20)
    definition: str = Field(min_length=1, max_length=500)
    consumer: str = Field(min_length=1, max_length=160)


class ComtradeSlice(ConfigModel):
    reporter: int = Field(ge=1, le=999)
    partner: int = Field(default=0, ge=0, le=999)
    hs_code: str = Field(pattern=r"^[0-9]{2,6}$")
    flow_code: str = Field(default="X,M", pattern=r"^[A-Z,]+$")
    history_years: int = Field(default=1, ge=1, le=10)
    consumer: str = Field(min_length=1, max_length=160)


def _world_bank_defaults() -> list[WorldBankIndicator]:
    rows = [
        ("NY.GDP.MKTP.CD", "Nominal GDP", "current USD"),
        ("NY.GDP.MKTP.KD", "Real GDP", "constant 2015 USD"),
        ("SP.POP.TOTL", "Population", "people"),
        ("NY.GNP.PCAP.CD", "Income per capita", "current USD"),
        ("GC.DOD.TOTL.GD.ZS", "Government debt", "% GDP"),
        ("DT.DOD.DECT.CD", "External debt", "current USD"),
        ("NV.IND.TOTL.ZS", "Industry value added", "% GDP"),
        ("NE.TRD.GNFS.ZS", "Trade openness", "% GDP"),
        ("EG.IMP.CONS.ZS", "Energy import dependence", "% energy use"),
    ]
    return [
        WorldBankIndicator(key=k, label=label, unit=unit, consumer="country context")
        for k, label, unit in rows
    ]


def _sdmx_defaults() -> list[PublicSdmxSeries]:
    oecd = "https://sdmx.oecd.org/public/rest/v1"
    bis = "https://stats.bis.org/api/v2"
    rows = [
        (
            "oecd",
            "inflation",
            oecd,
            "OECD.SDD.TPS,DSD_PRICES@DF_PRICES_ALL,1.0",
            ".M.CPI.PC_CHGPP",
            "US",
            "%",
            "monthly",
            "Consumer-price inflation; OECD definition",
        ),
        (
            "oecd",
            "unemployment",
            oecd,
            "OECD.SDD.TPS,DSD_LFS@DF_IALFS_UNE_M,1.0",
            ".M.Y_GE15.PS._T._T",
            "US",
            "%",
            "monthly",
            "Harmonised unemployment rate",
        ),
        (
            "oecd",
            "productivity",
            oecd,
            "OECD.SDD.PDB,DSD_PDB@DF_PDB_LV,",
            ".A....",
            "US",
            "index",
            "annual",
            "Labour productivity; configured OECD measure",
        ),
        (
            "oecd",
            "wages",
            oecd,
            "OECD.SDD.TPS,DSD_EARNINGS@DF_EARNINGS,",
            ".Q....",
            "US",
            "index",
            "quarterly",
            "Earnings index; configured OECD measure",
        ),
        (
            "oecd",
            "consumer_confidence",
            oecd,
            "OECD.SDD.STES,DSD_STES@DF_CLI,",
            ".M.CSCICP03",
            "US",
            "index",
            "monthly",
            "Consumer confidence indicator",
        ),
        (
            "oecd",
            "business_confidence",
            oecd,
            "OECD.SDD.STES,DSD_STES@DF_CLI,",
            ".M.BSCICP03",
            "US",
            "index",
            "monthly",
            "Business confidence indicator",
        ),
        (
            "oecd",
            "housing",
            oecd,
            "OECD.SDD.TPS,DSD_RHPI@DF_RHPI,",
            ".Q.RHPI",
            "US",
            "index",
            "quarterly",
            "Residential property price index",
        ),
        (
            "bis",
            "total_credit",
            bis,
            "BIS,WS_TC,2.0",
            ".Q.P.A.M.770.A",
            "US",
            "% GDP",
            "quarterly",
            "Credit to non-financial sector by borrower residence",
        ),
        (
            "bis",
            "debt_service",
            bis,
            "BIS,WS_DSR,1.0",
            ".Q.P.N",
            "US",
            "% income",
            "quarterly",
            "Private non-financial-sector debt service ratio",
        ),
        (
            "bis",
            "banking_claims",
            bis,
            "BIS,WS_LBS_D_PUB,1.0",
            ".Q.S.A.TO1",
            "US",
            "USD",
            "quarterly",
            "International banking claims by residence",
        ),
        (
            "bis",
            "debt_securities",
            bis,
            "BIS,WS_DEBT_SEC2_PUB,1.0",
            ".Q.C.1",
            "US",
            "USD",
            "quarterly",
            "International debt securities outstanding",
        ),
        (
            "bis",
            "global_liquidity",
            bis,
            "BIS,WS_GLI,1.0",
            ".Q.US",
            "US",
            "USD",
            "quarterly",
            "Global liquidity indicator",
        ),
        (
            "bis",
            "property_prices",
            bis,
            "BIS,WS_LONG_CPI,1.0",
            ".Q.R",
            "US",
            "index",
            "quarterly",
            "Residential property price index",
        ),
    ]
    return [
        PublicSdmxSeries(
            provider=provider,
            key=key,
            label=key.replace("_", " ").title(),
            base_url=url,
            flow=flow,
            series_key=series_key,
            geography=geo,
            unit=unit,
            frequency=frequency,
            definition=definition,
            consumer="holding country context",
        )
        for provider, key, url, flow, series_key, geo, unit, frequency, definition in rows
    ]


class PublicDataSettings(ConfigModel):
    """Curated Phase-2 requests. Every series names a product consumer."""

    countries: list[str] = Field(default_factory=lambda: ["CA", "CZ", "DE", "US", "CN", "IN"])
    world_bank: list[WorldBankIndicator] = Field(default_factory=_world_bank_defaults)
    sdmx: list[PublicSdmxSeries] = Field(default_factory=_sdmx_defaults)
    comtrade: list[ComtradeSlice] = Field(default_factory=list)


def _provider_defaults() -> dict[str, ProviderJobSettings]:
    quotas = {
        "alpha_vantage": (25, None),
        "eodhd": (20, None),
        "fmp": (250, None),
        "un_comtrade": (500, None),
        "diffbot": (None, 10_000),
        "people_data_labs": (None, 100),
        "twelve_data": (None, None),
        "finnhub": (None, None),
        "world_bank": (None, None),
        "oecd": (None, None),
        "bis": (None, None),
        "ecb": (None, None),
        "epo_ops": (None, None),
        "edinet": (500, None),
        "companies_house": (2000, None),
    }
    return {
        name: ProviderJobSettings(daily_quota=daily, monthly_quota=monthly)
        for name, (daily, monthly) in quotas.items()
    }


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
    stuck_run_minutes: int = Field(
        default=240,
        ge=0,
        le=2880,
        description="A run still going after this long alerts once (0: off). The largest "
        "jobs (13F loads, backfills) take an hour or more, so keep it above that.",
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
    rules_first: bool = Field(
        default=True,
        description="Answer a short, closed question about the portfolio's value or cash from "
        "the database with a template (no model call); everything else goes to the assistant.",
    )
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


FundKey = Annotated[str, Field(pattern=r"^[A-Za-z0-9._-]{1,32}$", examples=["CSPX"])]


class FundValuationSource(ConfigModel):
    """Where an ETF's fund-level P/E and P/B are published (they stand in for its index)."""

    key: FundKey
    label: str = Field(min_length=1, max_length=120)
    provider: Literal["ishares", "vanguard"]
    url: AnyHttpUrl = Field(
        description="iShares: the product page (its embedded JSON carries the figures). "
        "Vanguard: the product-data GraphQL endpoint (e.g. https://www.vanguard.co.uk/gpx/graphql)."
    )
    port_id: str | None = Field(
        default=None,
        pattern=r"^[0-9]{1,10}$",
        description="Vanguard only: the fund's port id (9679 = FTSE All-World UCITS ETF Acc).",
    )
    consumer_id: str = Field(
        default="uk0",
        pattern=r"^[a-z0-9]{1,10}$",
        description="Vanguard only: the site's x-consumer-id header (uk0 for vanguard.co.uk).",
    )
    tickers: list[Ticker] = Field(
        default_factory=list, description="Holdings whose valuation is this fund's."
    )
    index_label: str = Field(
        default="", max_length=80, description="The index the fund tracks, e.g. 'S&P 500'."
    )
    benchmark_symbols: list[StooqSymbol] = Field(
        default_factory=list,
        description="Benchmark symbols (Prices config) this fund's figures stand in for.",
    )

    @model_validator(mode="after")
    def _vanguard_needs_port(self) -> Self:
        if self.provider == "vanguard" and not self.port_id:
            raise ValueError("a Vanguard fund needs its port_id")
        return self


def _default_fund_sources() -> list[FundValuationSource]:
    return [
        FundValuationSource(
            key="CSPX",
            label="iShares Core S&P 500 UCITS ETF (Acc)",
            provider="ishares",
            url=AnyHttpUrl(
                "https://www.ishares.com/uk/individual/en/products/253743/"
                "ishares-sp-500-b-ucits-etf-acc-fund?switchLocale=y&siteEntryPassthrough=true"
            ),
            tickers=["CSPXl_EQ"],
            index_label="S&P 500",
            benchmark_symbols=["^spx"],
        ),
        FundValuationSource(
            key="VWCE",
            label="Vanguard FTSE All-World UCITS ETF (USD) Acc",
            provider="vanguard",
            url=AnyHttpUrl("https://www.vanguard.co.uk/gpx/graphql"),
            port_id="9679",
            tickers=["VWCEd_EQ"],
            index_label="FTSE All-World",
        ),
    ]


class ValuationSettings(ConfigModel):
    """Fundamentals from SEC XBRL and ESEF, with an optional Alpha Vantage ratio fallback."""

    enabled: bool = Field(default=True, description="Off: the scheduled run does nothing.")
    full_refresh_days: int = Field(
        default=7,
        ge=1,
        le=90,
        description="Each run re-reads a holding's fundamentals when the issuer filed a new "
        "report since the last read, and in any case after this many days.",
    )
    esef_max_age_months: int = Field(
        default=18,
        ge=3,
        le=60,
        description="ESEF filings (filings.xbrl.org) with an older period end are ignored.",
    )
    finnhub_cross_check: bool = Field(
        default=True,
        description="Also read Finnhub's basic financials (P/E, P/B, EPS) as a tier-2 "
        "cross-check. Needs FINNHUB_API_KEY; never required.",
    )
    alpha_vantage_fallback: bool = Field(
        default=True,
        description=(
            "Fill valuation metrics missing from SEC/ESEF with Alpha Vantage Company Overview. "
            "Needs ALPHA_VANTAGE_API_KEY; limited to 25 requests per UTC day."
        ),
    )
    funds: list[FundValuationSource] = Field(
        default_factory=_default_fund_sources,
        max_length=20,
        description="ETFs whose fund-level P/E and P/B are read from the provider.",
    )

    @model_validator(mode="after")
    def _unique_funds(self) -> Self:
        require_unique("fund keys", (f.key for f in self.funds))
        return self


Isin = Annotated[str, Field(pattern=r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$", examples=["IE00B5BMR087"])]
GicsSector = Literal[
    "Information Technology",
    "Financials",
    "Health Care",
    "Consumer Discretionary",
    "Communication Services",
    "Industrials",
    "Consumer Staples",
    "Energy",
    "Utilities",
    "Materials",
    "Real Estate",
]

CSPX_ISIN = "IE00B5BMR087"
VWCE_ISIN = "IE00BK5BQT80"


class LookThroughFund(ConfigModel):
    """A fund whose holdings are read from its issuer. Matched to a position by ISIN."""

    isin: Isin
    label: str = Field(min_length=1, max_length=40, examples=["CSPX"])
    provider: Literal["ishares", "vanguard"]
    portfolio_id: str = Field(
        pattern=r"^[A-Za-z0-9-]{1,24}$",
        description="The issuer's fund id: the iShares product/portfolio id (CSPX 253743) or "
        "the Vanguard Europe port id (VWCE 9679).",
    )


class BenchmarkProxy(ConfigModel):
    """Which fund's holdings stand in for a benchmark index's constituents."""

    symbol: StooqSymbol = Field(description="A benchmark symbol from prices.benchmark_symbols.")
    fund_isin: Isin
    label: str = Field(min_length=1, max_length=60, examples=["S&P 500"])


class IssuerGroup(ConfigModel):
    """Securities of one issuer counted together in the top underlying holdings (an ADR and
    its ordinary share, two share classes)."""

    label: str = Field(min_length=1, max_length=80)
    isins: list[Isin] = Field(min_length=2, max_length=10)


def _default_funds() -> list[LookThroughFund]:
    return [
        LookThroughFund(isin=CSPX_ISIN, label="CSPX", provider="ishares", portfolio_id="253743"),
        LookThroughFund(isin=VWCE_ISIN, label="VWCE", provider="vanguard", portfolio_id="9679"),
    ]


def _default_issuer_groups() -> list[IssuerGroup]:
    return [
        IssuerGroup(
            label="Taiwan Semiconductor Manufacturing", isins=["US8740391003", "TW0002330008"]
        ),
        IssuerGroup(label="Alphabet", isins=["US02079K3059", "US02079K1079"]),
    ]


class LookThroughSettings(ConfigModel):
    """ETF look-through: holdings of the funds the portfolio holds, from the issuers'
    (undocumented) data endpoints, stored weekly as dated snapshots."""

    enabled: bool = Field(
        default=True, description="Fetch from the issuers (off: stored snapshots are still shown)."
    )
    funds: list[LookThroughFund] = Field(
        default_factory=_default_funds,
        max_length=30,
        description="Funds looked through, by ISIN: provider and the issuer's fund id.",
    )
    benchmark_proxies: list[BenchmarkProxy] = Field(
        default_factory=lambda: [
            BenchmarkProxy(symbol="^spx", fund_isin=CSPX_ISIN, label="S&P 500")
        ],
        max_length=10,
        description="The benchmark's constituents are proxied by this fund's holdings (equity "
        "lines renormalised to 100%). A proxy fund is fetched even when it is not held.",
    )
    issuer_groups: list[IssuerGroup] = Field(
        default_factory=_default_issuer_groups,
        max_length=50,
        description="ISINs of one issuer counted together in the top underlying holdings.",
    )
    direct_sectors: dict[Ticker, GicsSector] = Field(
        default_factory=dict,
        description="Sector of a directly held stock when no fund holds the same ISIN and the "
        "strategy's sector bucket is not a GICS sector.",
    )
    direct_countries: dict[Ticker, Annotated[str, Field(pattern=r"^[A-Z]{2}$")]] = Field(
        default_factory=dict,
        description="Country of a directly held stock, overriding its home market.",
    )
    ishares_api_url: str = Field(
        default="https://www.blackrock.com/varnish-api/uk-retail01-product-data/product-data",
        pattern=r"^https://[^\s]+$",
    )
    ishares_site: str = Field(default="ishares-uk", pattern=r"^[a-z0-9-]{2,40}$")
    ishares_locale: str = Field(default="en_GB", pattern=r"^[a-z]{2}_[A-Z]{2}$")
    ishares_spreadsheet_fallback: bool = Field(
        default=True,
        description="When the holdings JSON fails and no recent snapshot is stored, read the "
        "full-fund download (no country or ISIN column).",
    )
    vanguard_graphql_url: str = Field(
        default="https://www.ie.vanguard/gpx/graphql", pattern=r"^https://[^\s]+$"
    )
    vanguard_consumer_id: str = Field(default="ie0", pattern=r"^[a-z]{2}[0-9]$")
    cached_fallback_days: int = Field(
        default=45,
        ge=1,
        le=400,
        description="On a failed fetch, a stored snapshot this recent is kept in use instead "
        "of trying the spreadsheet fallback.",
    )
    stale_after_days: int = Field(
        default=60,
        ge=7,
        le=400,
        description="A snapshot older than this is labelled stale (Vanguard publishes "
        "month-end data a few weeks late).",
    )
    keep_snapshots: int = Field(default=12, ge=1, le=200, description="Snapshots kept per fund.")

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        require_unique("look-through fund ISINs", (f.isin for f in self.funds))
        require_unique("benchmark proxy symbols", (b.symbol for b in self.benchmark_proxies))
        isins = {f.isin for f in self.funds}
        for proxy in self.benchmark_proxies:
            if proxy.fund_isin not in isins:
                raise ValueError(
                    f"benchmark proxy {proxy.symbol!r} names fund {proxy.fund_isin}, which is "
                    "not in funds"
                )
        return self

    def fund(self, isin: str | None) -> LookThroughFund | None:
        return next((f for f in self.funds if f.isin == isin), None)


class XbrlSettings(ConfigModel):
    """SEC XBRL fundamentals: shares outstanding and revenue by region / segment / product,
    read from each holding's latest 10-K / 10-Q / 20-F XBRL instance."""

    enabled: bool = True
    quarterly: bool = Field(
        default=True,
        description="Also parse the latest 10-Q when it is newer than the annual report "
        "(US issuers; 20-F filers publish annual XBRL only).",
    )
    read_ads_ratio: bool = Field(
        default=True,
        description="For 20-F filers, read the ADS ratio the annual report states ('each ADS "
        "represents five common shares') to check the configured share basis. Downloads the "
        "report's main document (up to about 10 MB) once per new 20-F.",
    )
    max_document_mb: int = Field(
        default=40, ge=1, le=200, description="Largest filing document downloaded."
    )


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
    providers: dict[str, ProviderJobSettings] = Field(
        default_factory=_provider_defaults,
        description="Per-provider refresh interval and hard free-tier budgets. A provider is "
        "used whenever it is available (its API key is set, or it needs none).",
    )
    public_data: PublicDataSettings = Field(default_factory=PublicDataSettings)
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
    public_data_collect: JobSchedule = Field(
        default_factory=lambda: JobSchedule(cron="0 4 * * 0"),
        description="Curated World Bank, OECD, BIS and UN Comtrade releases. A source is "
        "skipped until the source audit has verified its access. Default: Sundays 04:00.",
    )
    source_audit: JobSchedule = Field(
        default_factory=lambda: JobSchedule(cron="0 3 * * 1"),
        description="Probe every free endpoint once (one request each, inside its free quota) "
        "and record whether it is available, so the collectors use it. Also runs at worker "
        "start when a key was added or changed. Default: Mondays 03:00.",
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
    insider_trades: JobSchedule = Field(
        default_factory=lambda: JobSchedule(cron="30 6 * * *"),
        description=(
            "Load structured SEC Forms 3, 4 and 5 for portfolio issuers. Default: daily 06:30."
        ),
    )
    control_stakes: JobSchedule = Field(
        default_factory=lambda: JobSchedule(cron="45 6 * * *"),
        description="Load Schedule 13D/G beneficial-ownership filings for portfolio issuers. "
        "Default: daily 06:45.",
    )
    nport_holdings: JobSchedule = Field(
        default_factory=lambda: JobSchedule(cron="15 5 * * 6"),
        description="Load public SEC N-PORT fund holdings for held US funds. Default: Sat 05:15.",
    )
    fund_probes: JobSchedule = Field(
        default_factory=lambda: JobSchedule(cron="0 4 * * 0"),
        description="Probe optional Finnhub/FMP fund endpoints for free access. Default: "
        "Sundays 04:00.",
    )
    fund_characteristics_audit: JobSchedule = Field(
        default_factory=lambda: JobSchedule(cron="30 7 * * *"),
        description="Check iShares/Vanguard fund valuation figures for parser drift. "
        "Default: daily 07:30.",
    )
    free_source_alerts: JobSchedule = Field(
        default_factory=lambda: JobSchedule(cron="5 12 * * *"),
        description="Evaluate credit, trade, patent and graph alerts. Default: daily 12:05.",
    )
    shadow_snapshot: JobSchedule = Field(
        default_factory=lambda: JobSchedule(cron="50 23 * * *", enabled=False),
        description="Record the daily free-source shadow-run snapshot. Default: daily 23:50.",
    )
    shadow_report: JobSchedule = Field(
        default_factory=lambda: JobSchedule(cron="0 8 * * 1", enabled=False),
        description="Generate the free-source shadow-run report. Default: Monday 08:00.",
    )
    raw_retention: JobSchedule = Field(
        default_factory=lambda: JobSchedule(cron="15 3 * * *"),
        description=(
            "Blanks old raw payloads (normalized data stays) and deletes fetch-audit rows past the "
            "horizon. Default: daily 03:15."
        ),
    )
    zero_cost_gate: JobSchedule = Field(
        default_factory=lambda: JobSchedule(cron="20 3 * * *"),
        description=(
            "Audits the access ledger and the budgets collectors pass to the governor against the "
            "free quotas; reads only. Default: daily 03:20."
        ),
    )
    returns_refresh: JobSchedule = Field(
        default_factory=lambda: JobSchedule(cron="45 23 * * 1-5"),
        description=(
            "Compute and store price and total returns for held securities. "
            "Default: weekdays 23:45."
        ),
    )
    exchange_probes: JobSchedule = Field(
        default_factory=lambda: JobSchedule(cron="0 5 2 * *"),
        description=(
            "Probe free price endpoints per exchange (one held symbol each). Default: the 2nd "
            "of each month, 05:00."
        ),
    )
    ir_releases: JobSchedule = Field(
        default_factory=lambda: JobSchedule(cron="20 7 * * *"),
        description=(
            "Fetch and version registered issuer IR pages (guidance, consensus, capital return). "
            "Default: daily 07:20."
        ),
    )
    sec_fsds_backfill: JobSchedule = Field(
        default_factory=lambda: JobSchedule(cron="0 4 15 * *", enabled=False),
        description=(
            "Backfill statements and notes from the SEC Financial Statement and Notes "
            "datasets. Off by default."
        ),
    )
    pdmr_uk: JobSchedule = Field(
        default_factory=lambda: JobSchedule(cron="10 8 * * 1-5", enabled=False),
        description=(
            "Parse UK PDMR (MAR Art.19) notifications from the RNS stream. Off by "
            "default (retrieval unverified)."
        ),
    )
    graph_review: JobSchedule = Field(
        default_factory=lambda: JobSchedule(cron="40 7 * * *"),
        description=(
            "Review SEC relationship candidates, extract deal events and reconcile parent "
            "changes. Default: daily 07:40."
        ),
    )
    fmp_company: JobSchedule = Field(
        default_factory=lambda: JobSchedule(cron="20 9 * * 6"),
        description=(
            "FMP free tier: company profile (sector, industry) and annual analyst estimates "
            "of holdings that map an EDGAR/Finnhub ticker. Runs when FMP_API_KEY is set and "
            "the endpoint probe passed. At most two requests per holding, inside the "
            "provider's daily budget."
        ),
    )
    alpha_vantage_statements: JobSchedule = Field(
        default_factory=lambda: JobSchedule(cron="40 9 * * 6"),
        description=(
            "Alpha Vantage annual statements of holdings with a confirmed listing and an "
            "explicit symbol mapping, stalest first, a few per run so the shared 25-a-day "
            "budget keeps its reserve. Runs when ALPHA_VANTAGE_API_KEY is set."
        ),
    )
    epo_patents: JobSchedule = Field(
        default_factory=lambda: JobSchedule(cron="0 10 1 * *"),
        description=(
            "EPO OPS patent-family refresh for the stalest holdings (a few per run). Runs "
            "when the OPS client credentials are set."
        ),
    )
    companies_house_filings: JobSchedule = Field(
        default_factory=lambda: JobSchedule(cron="10 10 * * 6"),
        description=(
            "Companies House company profile and filing history of UK holdings whose profile "
            "has a companies_house_number. Runs when COMPANIES_HOUSE_API_KEY is set. Two "
            "requests per holding, at most 20 holdings a run."
        ),
    )
    edinet_filings: JobSchedule = Field(
        default_factory=lambda: JobSchedule(cron="30 10 * * *"),
        description=(
            "EDINET (Japan FSA) document list of the last days, kept for holdings whose profile "
            "has an edinet_code (list and links only, no XBRL parsing). Runs when "
            "EDINET_API_KEY is set."
        ),
    )
    twelve_data_corporate_actions: JobSchedule = Field(
        default_factory=lambda: JobSchedule(cron="50 10 * * 6"),
        description=(
            "Twelve Data dividends and splits of holdings with a confirmed listing, stored as "
            "suggested corporate actions for the owner to confirm. Runs when TWELVE_DATA_API_KEY "
            "is set. A few holdings per run inside the Basic plan's credits; a plan denial "
            "latches the endpoint as paid-only."
        ),
    )
    valuation_refresh: JobSchedule = Field(
        default_factory=lambda: JobSchedule(cron="45 7 * * *"),
        description="Fundamentals (SEC XBRL, ESEF) of holdings that filed since the last read "
        "(all of them weekly) and the ETF providers' fund P/E and P/B. Default: daily 07:45.",
    )
    etf_look_through: JobSchedule = Field(
        default_factory=lambda: JobSchedule(cron="40 6 * * 6"),
        description="ETF look-through: the funds' holdings from iShares and Vanguard, skipped "
        "when the as-of date has not moved. Default: Saturdays 06:40.",
    )
    sec_xbrl: JobSchedule = Field(
        default_factory=lambda: JobSchedule(cron="40 5 * * 2"),
        description="Shares outstanding and revenue by region / segment from the holdings' "
        "latest SEC XBRL filings; only filings not parsed yet are downloaded. Default: "
        "Tuesdays 05:40.",
    )
    ai_approvals: JobSchedule = Field(
        default_factory=lambda: JobSchedule(cron="10 7 * * *"),
        description="AI approval of suggested listings and peers (and the Finnhub peer "
        "refresh that feeds it). The owner can overturn any decision. Default: daily 07:10.",
    )
    ir_discovery: JobSchedule = Field(
        default_factory=lambda: JobSchedule(cron="30 7 * * 1"),
        description="Finds investor-relations pages for holdings that have none (web search, "
        "checked and approved by AI). Default: Mondays 07:30.",
    )
    gap_research: JobSchedule = Field(
        default_factory=lambda: JobSchedule(cron="0 8 * * 3"),
        description="Searches reliable web pages for facts the structured sources left empty "
        "(next report date, shares outstanding, description); AI approves them. Default: "
        "Wednesdays 08:00.",
    )
    exposure_build: JobSchedule = Field(
        default_factory=lambda: JobSchedule(cron="50 5 * * 2"),
        description="Builds each holding's revenue-by-country exposure from its latest annual "
        "SEC XBRL filing (reported basis). Your own exposure entries are never changed. "
        "Default: Tuesdays 05:50.",
    )
    disclosure_sources: JobSchedule = Field(
        default_factory=lambda: JobSchedule(cron="0 6 * * *"),
        description="Re-test every disclosure source, store the result, switch a failing one off "
        "and the SEC sources on when they pass. Default: daily 06:00.",
    )
    disclosure_ingest: JobSchedule = Field(
        default_factory=lambda: JobSchedule(cron="40 6 * * 1-5"),
        description="Fetch, store and parse the newest SEC annual and quarterly filings of held "
        "issuers. Skips itself until the SEC sources pass their access test. "
        "Default: weekdays 06:40.",
    )
    disclosure_identifiers: JobSchedule = Field(
        default_factory=lambda: JobSchedule(cron="20 6 * * 6"),
        description="Suggest missing LEIs (GLEIF) and FIGIs (OpenFIGI); you confirm each. "
        "Default: Saturdays 06:20.",
    )
    disclosure_estimates: JobSchedule = Field(
        default_factory=lambda: JobSchedule(cron="30 6 * * 1-5"),
        description="Fetch each enabled issuer consensus file once and store a new version only "
        "when its bytes changed. Default: weekdays 06:30.",
    )
    disclosure_alerts: JobSchedule = Field(
        default_factory=lambda: JobSchedule(cron="15 13 * * *"),
        description="Scan verified disclosures for new alerts and e-mail them, grouped by "
        "ticker, when e-mail is configured. Default: daily 13:15.",
    )
    earnings_check: EarningsCheckSettings = Field(default_factory=EarningsCheckSettings)
    exposure_build_settings: ExposureBuildSettings = Field(default_factory=ExposureBuildSettings)
    ir_discovery_settings: IrDiscoverySettings = Field(default_factory=IrDiscoverySettings)
    gap_research_settings: GapResearchSettings = Field(default_factory=GapResearchSettings)
    ai_approval: AiApprovalSettings = Field(default_factory=AiApprovalSettings)
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
    valuation: ValuationSettings = Field(default_factory=ValuationSettings)
    look_through: LookThroughSettings = Field(default_factory=LookThroughSettings)
    xbrl: XbrlSettings = Field(default_factory=XbrlSettings)

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
            "public_data_collect": self.public_data_collect,
            "source_audit": self.source_audit,
            "monitor": self.monitor,
            "news_ingest": self.news_ingest,
            "daily_digest": self.daily_digest,
            "weekly_digest": self.weekly_digest,
            "monthly_review": self.monthly_review,
            "ownership_13f": self.ownership_13f,
            "insider_trades": self.insider_trades,
            "valuation_refresh": self.valuation_refresh,
            "control_stakes": self.control_stakes,
            "nport_holdings": self.nport_holdings,
            "fund_probes": self.fund_probes,
            "fund_characteristics_audit": self.fund_characteristics_audit,
            "free_source_alerts": self.free_source_alerts,
            "shadow_snapshot": self.shadow_snapshot,
            "shadow_report": self.shadow_report,
            "raw_retention": self.raw_retention,
            "zero_cost_gate": self.zero_cost_gate,
            "returns_refresh": self.returns_refresh,
            "exchange_probes": self.exchange_probes,
            "ir_releases": self.ir_releases,
            "sec_fsds_backfill": self.sec_fsds_backfill,
            "pdmr_uk": self.pdmr_uk,
            "graph_review": self.graph_review,
            "fmp_company": self.fmp_company,
            "alpha_vantage_statements": self.alpha_vantage_statements,
            "epo_patents": self.epo_patents,
            "companies_house_filings": self.companies_house_filings,
            "edinet_filings": self.edinet_filings,
            "ai_approvals": self.ai_approvals,
            "ir_discovery": self.ir_discovery,
            "gap_research": self.gap_research,
            "exposure_build": self.exposure_build,
            "disclosure_sources": self.disclosure_sources,
            "disclosure_ingest": self.disclosure_ingest,
            "disclosure_identifiers": self.disclosure_identifiers,
            "disclosure_estimates": self.disclosure_estimates,
            "disclosure_alerts": self.disclosure_alerts,
            "twelve_data_corporate_actions": self.twelve_data_corporate_actions,
            "etf_look_through": self.etf_look_through,
            "sec_xbrl": self.sec_xbrl,
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
