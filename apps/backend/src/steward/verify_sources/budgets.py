"""Registry of the request budgets the collectors really pass to the governor (plan D8.5).

The zero-cost gate used to look only at the database seed rows, so a collector that asked its
governor for more than the free quota (or for no limit at all) went unnoticed.  Every place in
`src/steward` that builds a `Budget` is described here, so `audit_zero_cost` can compare what
the code would spend with what the access ledger says is free:

* `LITERAL_BUDGETS` - budgets written as constants in a collector (the entry names the file);
* `configured_budgets` - budgets derived from the job configuration's provider settings
  (`JobsConfig.providers`), including the caps `reference/prices.py` applies on top;
* `NO_OVERAGE_PROVIDERS` - credentialed providers whose free key has no pay-as-you-go
  mechanism: beyond the free limit they refuse (429) rather than bill, so the ledger needs no
  quota for them;
* `BUDGET_SITES` - how many `Budget(...)` constructions each source file contains.
  tests/test_zero_cost_phase8.py scans the source tree with `ast` and fails when a file's count
  differs, so a new collector budget cannot be added without registering it here.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from steward.sources.governor import Budget

ANY_ENDPOINT = "*"


@dataclass(frozen=True, slots=True)
class CollectorBudget:
    provider: str
    endpoint: str  # an endpoint name or ANY_ENDPOINT
    budget: Budget
    where: str  # source file (relative to src/) that builds it
    origin: str  # "literal" | "config"

    def covers(self, provider: str, endpoint: str) -> bool:
        return self.provider == provider and self.endpoint in (ANY_ENDPOINT, endpoint)


# Credentialed providers without pay-as-you-go overage: beyond the free limit they refuse (HTTP
# 429) or require an explicit plan change; none bills per call.  Reachable endpoints of these need
# no ledger quota to be provably zero-cost.  Re-verified from the providers' own pages on
# 2026-10-03 (see NO_OVERAGE_EVIDENCE and docs/data/provider-access.md); re-verify when a plan
# changes.
NO_OVERAGE_PROVIDERS: dict[str, str] = {
    "fred": "free API key; the terms list no fee or paid tier and reserve the right to limit or "
    "disable access",
    "finnhub": "free key; docs: 'If your limit is exceeded, you will receive a response with "
    "status code 429', plus a 30 calls/second cap on every plan; fixed subscription plans only",
    "twelve_data": "Basic plan is free with 8 credits/minute and 800/day; over the limit the API "
    "answers 429 'Wait briefly or upgrade your plan'; no hidden charges, upgrading is an explicit "
    "plan switch",
    "epo_ops": "non-paying use is free up to 4 GB/week; more than that is the EUR 2,800/year "
    "'paying user' subscription, which must be taken out explicitly",
}

# Where each claim above was read (official pages, fetched 2026-10-03) and what to watch.
NO_OVERAGE_EVIDENCE: dict[str, dict[str, str]] = {
    "fred": {
        "checked": "2026-10-03",
        "urls": "https://fred.stlouisfed.org/docs/api/terms_of_use.html; "
        "https://fred.stlouisfed.org/docs/api/api_key.html",
        "note": "The pages state no fee, quota or paid tier; the terms allow the Federal Reserve "
        "Bank of St. Louis to impose or adjust bandwidth/transaction limits. The numeric rate "
        "limit is not published there.",
    },
    "finnhub": {
        "checked": "2026-10-03",
        "urls": "https://finnhub.io/docs/api/rate-limit; https://finnhub.io/terms-of-service",
        "note": "429 on exceeding the limit; 30 calls/second on top of every plan. The pricing "
        "page is script-rendered and could not be read, so the free plan's per-minute figure "
        "is not re-confirmed here; the terms mention no usage-based charge.",
    },
    "twelve_data": {
        "checked": "2026-10-03",
        "urls": "https://twelvedata.com/pricing; https://twelvedata.com/docs (Errors)",
        "note": "Basic: free, 8 API credits/minute, 800/day, 'No contracts, no hidden charges'. "
        "LICENCE CAVEAT: the Basic plan states 'Internal non-display usage: data may be used "
        "internally for testing, evaluation, or development purposes only; it cannot be "
        "displayed to users, shared externally, or used in production systems'. Zero-cost "
        "holds; whether Twelve Data figures may be shown in this app is the owner's call.",
    },
    "epo_ops": {
        "checked": "2026-10-03",
        "urls": "https://www.epo.org/en/searching-for-patents/data/web-services/ops",
        "note": "'Non-paying users: up to 4 GB of data per week, free; paying users: more than "
        "4 GB per week, annual subscription EUR 2,800 (Subscribe as paying user)'.",
    },
}

# `Budget(` constructions per source file under src/steward (excluding verify_sources).
BUDGET_SITES: dict[str, int] = {
    "steward/free_sources.py": 2,
    "steward/macro/public_collect.py": 1,
    "steward/macro/release_calendar.py": 7,
    "steward/news/feeds.py": 1,
    "steward/reference/exchange_probes.py": 2,
    "steward/reference/prices.py": 3,
    "steward/sources/audit.py": 2,
    "steward/sources/company_research.py": 4,
    "steward/sources/fund_probes.py": 1,
    "steward/valuation/collect.py": 1,
}


# The three modules that build `settings_budget` for every provider they touch.
SETTINGS_SITES = (
    "steward/sources/audit.py, steward/sources/fund_probes.py, steward/macro/public_collect.py"
)


def _d(value: int | float | Decimal | None) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def settings_budget(settings: Any) -> Budget:
    """The budget `macro/public_collect`, `sources/audit` and `sources/fund_probes` build from a
    provider's job settings."""
    return Budget(
        daily=_d(settings.daily_quota),
        monthly=_d(settings.monthly_quota),
        reserve=Decimal(settings.reserve_credits),
    )


def capped_settings_budget(settings: Any, cap: int) -> Budget:
    """`reference/prices.py`: the daily allowance is never above `cap`, whatever is configured."""
    return Budget(
        daily=Decimal(min(settings.daily_quota or cap, cap)),
        monthly=_d(settings.monthly_quota) if settings.monthly_quota else None,
        reserve=Decimal(settings.reserve_credits),
    )


def literal_budgets() -> list[CollectorBudget]:
    from steward.macro.release_calendar import CALENDAR_BUDGETS, ENDPOINT
    from steward.reference.exchange_probes import FINNHUB_BUDGET, TWELVE_BUDGET
    from steward.valuation.collect import ALPHA_VANTAGE_DAILY_LIMIT

    av = "alpha_vantage"
    valuation = "steward/valuation/collect.py"
    feeds = "steward/news/feeds.py"
    research = "steward/sources/company_research.py"
    free = "steward/free_sources.py"
    audit = "steward/sources/audit.py"
    prices = "steward/reference/prices.py"
    d20, d25 = Budget(daily=Decimal(20)), Budget(daily=Decimal(25))
    rows: list[tuple[str, str, Budget, str]] = [
        (av, "account_free", Budget(daily=Decimal(ALPHA_VANTAGE_DAILY_LIMIT)), valuation),
        (av, "account_free", d20, feeds),
        (av, "news_sentiment", d20, feeds),
        (av, "account_free", d20, research),
        (av, "fx_daily", d20, research),
        (av, "commodity", d20, research),
        ("people_data_labs", "company_enrich", Budget(monthly=Decimal(100)), research),
        ("diffbot", "organization", Budget(monthly=Decimal(10_000)), research),
        ("epo_ops", "patent_search", Budget(per_minute=Decimal(1), monthly=Decimal(100)), research),
        (av, "account_free", d25, free),
        (av, "financial_statements", d25, free),
        ("finnhub", ANY_ENDPOINT, Budget(), free),
        (av, "account_free", d25, audit),
        (av, "account_free", d25, prices),
        # the module's own constants, so the registry cannot drift from them
        ("finnhub", ANY_ENDPOINT, FINNHUB_BUDGET, "steward/reference/exchange_probes.py"),
        ("twelve_data", ANY_ENDPOINT, TWELVE_BUDGET, "steward/reference/exchange_probes.py"),
        # the release-calendar collector's own constants (one per provider calendar)
        *(
            (provider, ENDPOINT, budget, "steward/macro/release_calendar.py")
            for provider, budget in CALENDAR_BUDGETS.items()
        ),
    ]
    return [CollectorBudget(p, e, b, w, "literal") for p, e, b, w in rows]


def configured_budgets(providers: Mapping[str, Any] | None = None) -> list[CollectorBudget]:
    """Budgets derived from the provider job settings (default: the shipped configuration)."""
    if providers is None:
        from steward.config.jobs import JobsConfig

        providers = JobsConfig().providers
    out: list[CollectorBudget] = []
    for name, settings in sorted(providers.items()):
        out.append(
            CollectorBudget(name, ANY_ENDPOINT, settings_budget(settings), SETTINGS_SITES, "config")
        )
    if "eodhd" in providers:
        out.append(
            CollectorBudget(
                "eodhd",
                ANY_ENDPOINT,
                capped_settings_budget(providers["eodhd"], 20),
                "steward/reference/prices.py",
                "config",
            )
        )
    if "alpha_vantage" in providers:
        out.append(
            CollectorBudget(
                "alpha_vantage",
                ANY_ENDPOINT,
                capped_settings_budget(providers["alpha_vantage"], 25),
                "steward/reference/prices.py",
                "config",
            )
        )
    return out


def collector_budgets(providers: Mapping[str, Any] | None = None) -> list[CollectorBudget]:
    return [*literal_budgets(), *configured_budgets(providers)]


def limit_in_period(budget: Budget, period: str | None) -> Decimal | None:
    """The budget's cap for the window a ledger quota is stated in (None = no cap there)."""
    return {"minute": budget.per_minute, "day": budget.daily, "month": budget.monthly}.get(
        period or ""
    )


def has_any_limit(budget: Budget) -> bool:
    return any(v is not None for v in (budget.per_minute, budget.daily, budget.monthly))
