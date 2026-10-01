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


# Credentialed providers without pay-as-you-go overage (stated from the providers' public
# pricing pages; re-verify when a plan changes).  Reachable endpoints of these need no ledger
# quota to be provably zero-cost.
NO_OVERAGE_PROVIDERS: dict[str, str] = {
    "fred": "free API key, no paid tier; requests over the rate limit are refused",
    "finnhub": "free key limited to 60 calls/minute; over the limit answers 429, never bills",
    "twelve_data": "Basic plan has fixed daily/minute credits; over them answers 429, never bills",
    "epo_ops": "non-paying OPS use has a weekly volume allowance; over it requests are "
    "throttled or refused, and billing needs a signed contract",
}

# `Budget(` constructions per source file under src/steward (excluding verify_sources).
BUDGET_SITES: dict[str, int] = {
    "steward/free_sources.py": 2,
    "steward/macro/public_collect.py": 1,
    "steward/news/feeds.py": 1,
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
