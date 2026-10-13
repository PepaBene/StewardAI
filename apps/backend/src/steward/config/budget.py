"""Budget config: the model price table every AI step is costed with.

Prices are the provider's list prices, but they change and differ per account (discounts,
new models), so they are config: a step's cost is its tokens and searches times the prices
here, recorded next to the step.

It also holds the spend limits (enforced by `steward.budget_guard` before every AI request of
a job): a monthly cap with an early warning, what happens to non-critical jobs at the cap,
and caps per single run. Every limit has a default that keeps older saved versions valid.
"""

from typing import Literal, Self

from pydantic import Field, model_validator

from steward.config.common import ConfigModel, require_unique
from steward.config.jobs import ModelId

# Every job type of `steward.jobs.registry.JOB_HANDLERS` (the registry imports every job
# module, so config cannot import it; a test keeps the two lists equal).
BudgetJobType = Literal[
    "portfolio_sync",
    "earnings_check",
    "thesis_verdicts",
    "earnings_calendar",
    "earnings_calendar_full",
    "earnings_due",
    "fact_reconcile",
    "reference_data",
    "price_history",
    "intraday_prices",
    "holiday_refresh",
    "macro_collect",
    "public_data_collect",
    "source_audit",
    "event_escalation",
    "monitor",
    "news_ingest",
    "daily_digest",
    "weekly_digest",
    "research",
    "ownership_13f",
    "insider_trades",
    "etf_look_through",
    "monthly_review",
    "chat",
    "sec_xbrl",
    "valuation_refresh",
    "control_stakes",
    "nport_holdings",
    "fund_probes",
    "fund_characteristics_audit",
    "fund_issuer_facts",
    "free_source_alerts",
    "change_alerts",
    "shadow_snapshot",
    "shadow_report",
    "raw_retention",
    "research_trace_retention",
    "zero_cost_gate",
    "returns_refresh",
    "exchange_probes",
    "ir_releases",
    "sec_fsds_backfill",
    "pdmr_uk",
    "graph_review",
    "fmp_company",
    "alpha_vantage_statements",
    "epo_patents",
    "companies_house_filings",
    "edinet_filings",
    "twelve_data_corporate_actions",
    "ai_approvals",
    "autopilot",
    "autopilot_calibration",
    "ir_discovery",
    "news_ai_search",
    "news_triage",
    "news_analysis",
    "news_brief",
    "gap_research",
    "exposure_build",
    "document_index",
    "inbox_scan",
    "inbox_agent",
    "factor_returns",
    "disclosure_sources",
    "disclosure_ingest",
    "disclosure_identifiers",
    "disclosure_deals",
    "disclosure_esef",
    "disclosure_estimates",
    "disclosure_alerts",
]

AtCap = Literal["alert_only", "pause_noncritical", "downgrade_noncritical"]

# Jobs that keep running at the monthly cap: checking a new earnings report against the
# theses, judging a severe news event, and answering the owner's own chat questions are
# what the app is for and are time-critical; digests, research and reviews can wait.
DEFAULT_CRITICAL_JOB_TYPES: list[BudgetJobType] = ["earnings_check", "event_escalation", "chat"]
DEFAULT_MONTHLY_CAPS_USD: dict[BudgetJobType, float] = {
    "news_analysis": 4.0,
    "news_brief": 1.5,
    "news_ai_search": 2.5,
    "news_triage": 1.0,
}


class ModelPrice(ConfigModel):
    model: ModelId
    input_per_mtok: float = Field(ge=0, description="USD per million input tokens.")
    output_per_mtok: float = Field(ge=0, description="USD per million output tokens.")
    # Providers differ in what a cached token costs and what a search costs, so each model can
    # override the global values below (null: use the global one).
    cache_write_multiplier: float | None = Field(
        default=None, ge=0, description="Overrides the global cache-write multiplier."
    )
    cache_read_multiplier: float | None = Field(
        default=None, ge=0, description="Overrides the global cache-read multiplier."
    )
    web_search_per_1000: float | None = Field(
        default=None, ge=0, description="Overrides the global price of 1,000 web searches."
    )


def _default_prices() -> list[ModelPrice]:
    prices: list[ModelPrice] = []
    # Embedding model: input only. PROVISIONAL list price (the plan flags it unverified; the
    # embedding API reports no usage, so tokens are estimated). Check against the provider's
    # price page and correct here; the news embedding cost is ~$1/month at this rate.
    prices.append(ModelPrice(model="gemini-embedding-2", input_per_mtok=0.15, output_per_mtok=0))
    # OpenAI and Google list prices of 2026-10-06 (docs/plans/multi_provider_model_routing_plan.md).
    # Cached input is read at 5 % (sol) or 10 % (luna, flash) of input with no write premium.
    # Gemini 3.x prices double on 2027-01-01: re-price flash then.
    prices += [
        ModelPrice(
            model="gpt-6.1-sol",
            input_per_mtok=2,
            output_per_mtok=10,
            cache_write_multiplier=1,
            cache_read_multiplier=0.05,
            web_search_per_1000=10,
        ),
        ModelPrice(
            model="gpt-6-luna",
            input_per_mtok=0.1,
            output_per_mtok=0.5,
            cache_write_multiplier=1,
            cache_read_multiplier=0.1,
            web_search_per_1000=10,
        ),
        ModelPrice(
            model="gemini-3.8-flash",
            input_per_mtok=0.75,
            output_per_mtok=3.75,
            cache_write_multiplier=1,
            cache_read_multiplier=0.1,
            web_search_per_1000=14,
        ),
    ]
    return prices


class BudgetConfig(ConfigModel):
    """Stored as `config_version` rows, domain `budget`."""

    schema_version: Literal[1] = 1
    prices: list[ModelPrice] = Field(default_factory=_default_prices)
    web_search_per_1000: float = Field(default=10, ge=0, description="USD per 1,000 web searches.")
    cache_write_multiplier: float = Field(
        default=1.25, ge=0, description="Cache-write tokens cost this times the input price."
    )
    cache_read_multiplier: float = Field(
        default=0.1, ge=0, description="Cache-read tokens cost this times the input price."
    )
    batch_multiplier: float = Field(
        default=0.5,
        ge=0,
        le=1,
        description="Batch API requests cost this times the token price (searches excluded).",
    )

    monthly_cap_usd: float | None = Field(
        default=None,
        gt=0,
        description="Most AI spend per calendar month (Europe/Prague), in USD. Null: no cap.",
    )
    warn_at_fraction: float = Field(
        default=0.8,
        gt=0,
        le=1,
        description="One alert per month when spend passes this fraction of the monthly cap.",
    )
    at_cap: AtCap = Field(
        default="pause_noncritical",
        description="At the monthly cap: only alert; fail non-critical jobs before their next"
        " AI request; or run their requests on `downgrade_model`.",
    )
    downgrade_model: ModelId = Field(
        default="gpt-6-luna",
        description="The model non-critical requests use at the cap (`downgrade_noncritical`).",
    )
    critical_job_types: list[BudgetJobType] = Field(
        default_factory=lambda: list(DEFAULT_CRITICAL_JOB_TYPES),
        description="Job types that run normally at the monthly cap.",
    )
    per_job_cap_usd: dict[BudgetJobType, float] = Field(
        default_factory=dict,
        description="Most AI spend of one run of a job type, in USD. A run over it fails.",
    )
    default_job_cap_usd: float | None = Field(
        default=None,
        gt=0,
        description="Most AI spend of one run of a job type not in `per_job_cap_usd`."
        " Null: no cap.",
    )
    per_job_monthly_cap_usd: dict[BudgetJobType, float] = Field(
        # The $10 news envelope (news plan section 8a): the new news stages degrade at their own
        # ceiling and never stop ingestion. Ingestion itself has no monthly cap here.
        default_factory=lambda: dict(DEFAULT_MONTHLY_CAPS_USD),
        description="Most AI spend of one job type per calendar month (Europe/Prague), in USD,"
        " so one stage cannot use the whole monthly budget. A run over it fails before its next"
        " request, whatever its criticality.",
    )

    @model_validator(mode="after")
    def _one_price_per_model(self) -> Self:
        require_unique("price model", [p.model for p in self.prices])
        require_unique("critical job type", self.critical_job_types)
        for job_type, cap in self.per_job_cap_usd.items():
            if cap <= 0:
                raise ValueError(f"per_job_cap_usd[{job_type}] must be greater than 0")
        for job_type, cap in self.per_job_monthly_cap_usd.items():
            if cap <= 0:
                raise ValueError(f"per_job_monthly_cap_usd[{job_type}] must be greater than 0")
        if "news_ingest" in {str(k) for k in self.per_job_monthly_cap_usd}:
            # A monthly cap raises BudgetExceeded and stops the run; the news plan says a capped
            # stage degrades and never stops ingestion, so ingestion cannot have one.
            raise ValueError("per_job_monthly_cap_usd cannot cap news_ingest: it must not stop")
        if self.at_cap == "downgrade_noncritical" and not any(
            p.model == self.downgrade_model for p in self.prices
        ):
            raise ValueError(
                f"downgrade_model {self.downgrade_model} has no price: add it to the price table"
            )
        return self

    def job_cap(self, job_type: str) -> float | None:
        """The spend cap of one run of `job_type` (None: none)."""
        caps = {str(k): v for k, v in self.per_job_cap_usd.items()}
        return caps.get(job_type, self.default_job_cap_usd)

    def job_monthly_cap(self, job_type: str) -> float | None:
        """The spend cap of one job type per month (None: none)."""
        return {str(k): v for k, v in self.per_job_monthly_cap_usd.items()}.get(job_type)
