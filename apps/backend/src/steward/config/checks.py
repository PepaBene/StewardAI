# ruff: noqa: E501
"""Investment rules for the insight layer (plan I3.10, I1.2, I1.4, I2.4, I5.6, I6.4).

Stored as `config_version` rows, domain `checks`, so every limit is versioned, diffable and
replayable like the other config domains. The numbers are StewardAI's own named rules with
editable thresholds, not a reproduction of any vendor's scoring. A check result names the config
version it was computed under through its `method_version` and the limits it quotes.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from steward.config.common import ConfigModel


class CheckLimits(ConfigModel):
    """Numeric limits of the five-axis checks and the risk checks (defaults from plan §9)."""

    payout_limit_earnings: float = Field(
        default=0.75, gt=0, le=2, description="Dividends paid / net income at or below this passes."
    )
    payout_limit_cash: float = Field(
        default=0.75, gt=0, le=2, description="Dividends paid / free cash flow at or below this."
    )
    roe_floor: float = Field(default=0.15, ge=-1, le=2, description="Return on equity floor.")
    roce_floor: float = Field(
        default=0.15, ge=-1, le=2, description="Return on capital employed floor."
    )
    forecast_roe_floor: float = Field(default=0.15, ge=-1, le=2)
    interest_coverage_min: float = Field(
        default=5, ge=0, le=100, description="EBIT / interest, times."
    )
    debt_to_equity_max: float = Field(default=0.40, ge=0, le=20)
    net_debt_to_ebitda_max: float = Field(default=3.0, ge=0, le=20)
    cfo_to_debt_min: float = Field(
        default=0.20, ge=0, le=5, description="Operating cash flow / total debt at or above this."
    )
    dilution_max: float = Field(default=0.05, ge=0, le=1, description="Share count change a year.")
    accruals_max: float = Field(default=0.10, ge=0, le=1)
    peg_max: float = Field(default=1.5, gt=0, le=10)
    fcf_yield_margin: float = Field(
        default=0.01,
        ge=-0.1,
        le=0.2,
        description="Free cash flow yield must beat the scenario risk-free rate by this margin.",
    )
    analyst_count_min: int = Field(default=5, ge=1, le=100)
    dividend_yield_floor: float = Field(
        default=0.01,
        ge=0,
        le=0.2,
        description="Below this yield the 'yield versus peers' check does not apply.",
    )
    dividend_growth_years: int = Field(default=10, ge=3, le=20)
    no_cut_years_min: int = Field(default=10, ge=1, le=30)
    small_cap_max: float = Field(
        default=2_000_000_000,
        ge=0,
        description="Market cap (listing currency) below this is small.",
    )
    liquidity_floor: float = Field(
        default=1_000_000,
        ge=0,
        description="Average daily turnover (listing currency) below this is illiquid.",
    )
    volatility_vs_benchmark_max: float = Field(
        default=1.5,
        gt=0,
        le=10,
        description="Stock volatility / benchmark volatility above this fails.",
    )
    benchmark_volatility: float = Field(
        default=0.16,
        gt=0,
        le=2,
        description="Annual benchmark volatility the ratio is taken against.",
    )
    debt_maturity_share_max: float = Field(
        default=0.25,
        ge=0,
        le=1,
        description="Debt due within a year / total debt above this fails.",
    )
    earnings_window_days: int = Field(default=14, ge=1, le=90)
    insider_window_days: int = Field(default=90, ge=7, le=365)


class DividendRules(ConfigModel):
    auto_confirm: bool = Field(
        default=True,
        description="Confirm a suggested dividend event automatically when a Trading212 payment "
        "for the same holding falls in its pay-date window and the amounts agree.",
    )
    auto_confirm_tolerance: float = Field(
        default=0.35,
        ge=0,
        le=1,
        description="Largest gap between the declared gross and the received amount (net of "
        "withholding) as a share of the declared amount that still counts as agreeing.",
    )
    confirm_from_filings: bool = Field(
        default=True,
        description="Confirm a suggested dividend event automatically when the issuer's filed "
        "per-share dividend for a nearby quarter agrees (history from before the holding has no "
        "payment to match). Older history is confirmed only when the filings agree with at "
        "least four of the holding's events and contradict none.",
    )
    match_window_days: int = Field(
        default=21,
        ge=1,
        le=90,
        description="Days around the pay date (or ex-date) a payment may land.",
    )
    assume_growth: bool = Field(
        default=False,
        description="Grow estimated payments by the holding's 5-year dividend growth. Off: the "
        "last amount is repeated.",
    )
    min_payments_for_estimate: int = Field(default=4, ge=2, le=12)
    single_source_history: bool = Field(
        default=True,
        description="Count suggested dividend events that only one source reports (Yahoo, while "
        "Twelve Data's dividends are not on the plan) as payment history for cadence, estimates "
        "and growth. Each is labelled single-source; none counts as declared until confirmed. "
        "Off: history needs a confirmed event, a Trading212 receipt or two sources agreeing.",
    )
    # A two-item list rather than a tuple, so the schema is one array type in and out.
    tier_cuts: list[float] = Field(
        default_factory=lambda: [1 / 3, 2 / 3],
        min_length=2,
        max_length=2,
        description="Quality tier boundaries as shares of the applicable dividend score: below "
        "the first is low, below the second medium, otherwise high.",
    )

    @model_validator(mode="after")
    def _ordered(self) -> DividendRules:
        low, high = self.tier_cuts
        if not 0 < low < high < 1:
            raise ValueError("tier cuts must rise strictly between 0 and 1")
        return self


class FairValueRules(ConfigModel):
    stale_after_days: int = Field(default=90, ge=7, le=730)
    move_alert: float = Field(
        default=0.10,
        ge=0,
        le=1,
        description="Alert when refreshed fundamentals move the scenario value by more than this share.",
    )
    drift_band: float = Field(
        default=0.05,
        ge=0,
        le=1,
        description="A multiple-path scenario drifts when the last four quarters' revenue growth "
        "or net margin departs from the assumed path by more than this (absolute).",
    )
    fair_multiple_min_r2: float = Field(
        default=0.3,
        ge=0,
        le=1,
        description="The fair-multiple regression of peer P/E on growth and margin is shown only "
        "when its R squared reaches this.",
    )
    agreement_close: float = Field(
        default=0.25,
        ge=0,
        le=5,
        description="Analysts agree closely when (high target - low target) / mean target is below this.",
    )
    agreement_wide: float = Field(
        default=0.5,
        ge=0,
        le=5,
        description="Analysts disagree widely when the target spread over the mean reaches this.",
    )
    consensus_chart_min_snapshots: int = Field(
        default=8,
        ge=2,
        le=104,
        description="Stored consensus snapshots needed before the target chart is drawn.",
    )

    @model_validator(mode="after")
    def _agreement_order(self) -> FairValueRules:
        if self.agreement_close >= self.agreement_wide:
            raise ValueError("the close-agreement cut must be below the wide-disagreement cut")
        return self


class ChecksConfig(ConfigModel):
    """Every limit the checks, the dividend engine and the fair-value recorder apply."""

    schema_version: Literal[1] = 1
    limits: CheckLimits = Field(default_factory=CheckLimits)
    dividends: DividendRules = Field(default_factory=DividendRules)
    fair_value: FairValueRules = Field(default_factory=FairValueRules)


async def load_checks(conn: Any) -> tuple[ChecksConfig, int | None]:
    """The active rules and their config version id; the defaults (id None) before the first save."""
    from steward.config.store import load_active

    stored = await load_active(conn, "checks", ChecksConfig)
    if stored is None:
        return ChecksConfig(), None
    return stored.body, stored.id
