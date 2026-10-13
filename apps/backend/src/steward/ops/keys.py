"""The key concierge (D2): which API key is missing, what it would unlock and where to get it.

Only keys for jobs that are switched on are listed, most valuable first. The owner's only step
is pasting the key into the app's environment; everything else is named here.
"""

from dataclasses import dataclass

from pydantic import SecretStr


@dataclass(frozen=True)
class KeyInfo:
    setting: str  # attribute on `Settings`
    env: str  # the environment variable to set
    name: str
    unlocks: str
    url: str
    jobs: tuple[str, ...]  # job types that use it; empty: always relevant
    priority: float  # 0..1, how much data or capability it adds


CATALOGUE: tuple[KeyInfo, ...] = (
    KeyInfo(
        "openai_api_key",
        "OPENAI_API_KEY",
        "OpenAI",
        "every AI step: research, news analysis, the autopilot's judgments",
        "https://platform.openai.com/api-keys",
        (),
        1.0,
    ),
    KeyInfo(
        "brave_api_key",
        "BRAVE_API_KEY",
        "Brave Search",
        "web search for missing facts and news discovery",
        "https://api-dashboard.search.brave.com/register",
        ("gap_research", "news_ai_search", "ir_discovery"),
        0.7,
    ),
    KeyInfo(
        "finnhub_api_key",
        "FINNHUB_API_KEY",
        "Finnhub",
        "earnings calendar, quotes and company news",
        "https://finnhub.io/register",
        ("earnings_calendar", "earnings_calendar_full"),
        0.6,
    ),
    KeyInfo(
        "fmp_api_key",
        "FMP_API_KEY",
        "Financial Modeling Prep",
        "company profiles, statements and executive pay",
        "https://site.financialmodelingprep.com/developer/docs",
        ("fmp_company",),
        0.5,
    ),
    KeyInfo(
        "eodhd_api_key",
        "EODHD_API_KEY",
        "EODHD",
        "a second price source for European listings",
        "https://eodhd.com/register",
        ("price_history",),
        0.5,
    ),
    KeyInfo(
        "alpha_vantage_api_key",
        "ALPHA_VANTAGE_API_KEY",
        "Alpha Vantage",
        "financial statements as a cross-check",
        "https://www.alphavantage.co/support/#api-key",
        ("alpha_vantage_statements",),
        0.4,
    ),
    KeyInfo(
        "twelve_data_api_key",
        "TWELVE_DATA_API_KEY",
        "Twelve Data",
        "corporate actions (splits and dividends)",
        "https://twelvedata.com/register",
        ("twelve_data_corporate_actions",),
        0.4,
    ),
    KeyInfo(
        "fred_api_key",
        "FRED_API_KEY",
        "FRED",
        "US macro series",
        "https://fredaccount.stlouisfed.org/apikeys",
        ("macro_collect",),
        0.3,
    ),
    KeyInfo(
        "edinet_api_key",
        "EDINET_API_KEY",
        "EDINET",
        "Japanese filings",
        "https://disclosure2dl.edinet-fsa.go.jp/guide/static/disclosure/WZEK0110.html",
        ("edinet_filings",),
        0.3,
    ),
    KeyInfo(
        "companies_house_api_key",
        "COMPANIES_HOUSE_API_KEY",
        "Companies House",
        "UK filings",
        "https://developer.company-information.service.gov.uk/",
        ("companies_house_filings",),
        0.3,
    ),
    KeyInfo(
        "epo_ops_client_secret",
        "EPO_OPS_CLIENT_SECRET",
        "EPO Open Patent Services",
        "patent families and citations",
        "https://developers.epo.org/",
        ("epo_patents",),
        0.2,
    ),
    KeyInfo(
        "openfigi_api_key",
        "OPENFIGI_API_KEY",
        "OpenFIGI",
        "a higher rate limit for identifier lookups",
        "https://www.openfigi.com/api",
        ("reference_data",),
        0.2,
    ),
    KeyInfo(
        "resend_api_key",
        "RESEND_API_KEY",
        "Resend",
        "e-mail alerts and digests",
        "https://resend.com/api-keys",
        ("daily_digest", "weekly_digest", "disclosure_alerts"),
        0.6,
    ),
    KeyInfo(
        "telegram_bot_token",
        "TELEGRAM_BOT_TOKEN",
        "Telegram bot",
        "instant alerts on your phone",
        "https://core.telegram.org/bots#how-do-i-create-a-bot",
        (),
        0.5,
    ),
)


def is_set(value: object) -> bool:
    if isinstance(value, SecretStr):
        return bool(value.get_secret_value())
    return bool(value)


def missing(settings: object, enabled_jobs: set[str]) -> list[KeyInfo]:
    """Keys not set whose jobs are on, most valuable first."""
    out = [
        k
        for k in CATALOGUE
        if not is_set(getattr(settings, k.setting, None))
        and (not k.jobs or any(j in enabled_jobs for j in k.jobs))
    ]
    return sorted(out, key=lambda k: -k.priority)
