"""Strategy config: goals, target allocation with bands, and the structured rules code evaluates."""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from steward.config.common import (
    ComparisonOperator,
    ConfigModel,
    CurrencyCode,
    Severity,
    Slug,
    Ticker,
    Weight,
    require_unique,
)

# Allocation dimensions are free-form names; buckets within them are free-form labels.
Dimension = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,31}$", examples=["sector"])]
Bucket = Annotated[str, Field(min_length=1, max_length=64, examples=["Semiconductors"])]

_WEIGHT_TOLERANCE = 1e-9


class TargetBand(ConfigModel):
    target_weight: Weight
    band: Weight = Field(
        default=0.02,
        description="Allowed absolute deviation from the target, as a fraction (0.02 = ±2 pp).",
    )


class PositionTarget(TargetBand):
    ticker: Ticker
    hard_limit: Weight | None = Field(
        default=None,
        description="Weight above which the position raises an alert, not just a digest flag.",
    )

    @model_validator(mode="after")
    def _hard_limit_not_below_target(self) -> Self:
        if self.hard_limit is not None and self.hard_limit < self.target_weight:
            raise ValueError("hard_limit must not be below target_weight")
        return self


class CategoryTarget(TargetBand):
    dimension: Dimension
    bucket: Bucket


class _RuleBase(ConfigModel):
    id: Slug
    operator: ComparisonOperator
    on_breach: Severity = "severity_2"
    description: str = ""
    enabled: bool = Field(
        default=True,
        description="A disabled rule is kept in the config (and still validated) but never "
        "evaluated: no breach, no review compliance row, not offered to the AI.",
    )


class PositionWeightRule(_RuleBase):
    """Limit on a single position's weight; with no ticker it applies to every position."""

    type: Literal["position_weight"]
    value: Weight
    ticker: Ticker | None = None


class CategoryWeightRule(_RuleBase):
    """Limit on a category's weight; with no bucket it applies to every bucket of the dimension."""

    type: Literal["category_weight"]
    value: Weight
    dimension: Dimension
    bucket: Bucket | None = None


class CashWeightRule(_RuleBase):
    type: Literal["cash_weight"]
    value: Weight


class PositionCountRule(_RuleBase):
    type: Literal["position_count"]
    value: int = Field(ge=0)


StrategyRule = Annotated[
    PositionWeightRule | CategoryWeightRule | CashWeightRule | PositionCountRule,
    Field(discriminator="type"),
]


class PretradeThresholds(ConfigModel):
    """The thresholds the pre-trade check uses for principles 8, 9 and 10. The defaults are the
    strategy's stated values; a check run without its own rules reads these."""

    return_hurdle: float = Field(default=0.30, ge=0, le=5)
    asymmetry_gate: float = Field(default=2.0, ge=0, le=20)
    start_size_core: float = Field(default=0.05, ge=0, le=1)
    start_size_growth: float = Field(default=0.03, ge=0, le=1)
    start_size_speculative: float = Field(default=0.015, ge=0, le=1)
    switch_safety_margin: float = Field(default=0.05, ge=0, le=1)
    short_term_tax_rate: float = Field(default=0.15, ge=0, le=1)
    trade_cost_bps: float = Field(default=0, ge=0, le=500)
    fx_cost_bps: float = Field(default=15, ge=0, le=500)
    risk_contribution_cap: float | None = Field(
        default=None,
        gt=0,
        le=1,
        description="Largest share of portfolio volatility one stock may account for (null: "
        "shown but not judged).",
    )


class RiskLimits(ConfigModel):
    """Portfolio risk limits, checked after each sync. Every limit is optional (null = off).

    A breach here is a prompt for review, not an instruction to trade: the strategy decides
    buys and sells on fundamentals and news, so a price-based limit only forces a thesis review.
    """

    max_volatility: Weight | None = Field(
        default=None,
        description="Ceiling on the portfolio's annualised volatility (0.20 = 20 %). Tested two "
        "ways: realized, on the time-weighted daily returns over the last year, and as a "
        "forecast from today's weights (the higher of the EWMA and Ledoit-Wolf covariance "
        "models, cash at zero volatility). A breach of either forces a thesis review.",
    )
    max_beta: float | None = Field(
        default=None,
        gt=0,
        le=5,
        description="Ceiling on the portfolio's beta against the configured benchmark, "
        "measured on daily returns over the last year.",
    )
    position_loss_review: Weight | None = Field(
        default=None,
        description="A position down this fraction or more from its cost (0.25 = -25 %) needs "
        "a thesis review. It never means 'sell'.",
    )
    max_look_through_weight: Weight | None = Field(
        default=None,
        description="Ceiling on the effective weight of one underlying company reached "
        "through funds/ETFs (plus any direct holding of it): the overlap limit.",
    )
    max_sector_weight: Weight | None = Field(
        default=None,
        description="Ceiling on one sector's share of the whole portfolio, counting funds' "
        "holdings (X-Ray). Weight with no sector is left out, not counted as a sector.",
    )
    max_country_weight: Weight | None = Field(
        default=None,
        description="Ceiling on one country's share of the whole portfolio, counting funds' "
        "holdings (X-Ray). Weight with no country is left out, not counted as a country.",
    )
    max_drawdown: Weight | None = Field(
        default=None,
        description="Ceiling on the portfolio's current drawdown: its fall from the highest "
        "level of the last year (0.20 = 20 % below the peak), on the deposit- and "
        "withdrawal-adjusted value.",
    )
    max_pair_correlation: Weight | None = Field(
        default=None,
        description="Ceiling on the correlation of two held positions' weekly base-currency "
        "returns over the last two years (0.85). Only pairs weighing at least "
        "`correlation_min_combined_weight` "
        "together are checked, so small positions do not raise noise.",
    )
    correlation_min_combined_weight: Weight = Field(
        default=0.10,
        description="Combined weight, of the invested value, a correlated pair must reach "
        "before `max_pair_correlation` applies.",
    )
    max_var_95: Weight | None = Field(
        default=None,
        description="Ceiling on the portfolio's one-day historical 95 % value at risk, as a share "
        "of the portfolio (0.03 = 3 %), from the last year of daily returns. A descriptive "
        "estimate of the sample, not a forecast.",
    )
    max_tracking_error: Weight | None = Field(
        default=None,
        description="Ceiling on the annualised tracking error against the configured benchmark "
        "(0.08 = 8 %), from the last year of daily returns.",
    )
    max_stress_loss: Weight | None = Field(
        default=None,
        description="Ceiling on the portfolio's loss when equities fall by `stress_equity_fall` "
        "(scenario engine: measured betas where valid, 1.0 otherwise).",
    )
    stress_equity_fall: Weight = Field(
        default=0.20, description="Size of the equity fall the stress loss is measured against."
    )
    credit_spread_alert_bp: float | None = Field(
        default=None,
        gt=0,
        le=3000,
        description="Alert when the stored US high-yield credit spread (series us_hy_spread) is "
        "at or above this level, in basis points. A market signal, not a portfolio measure.",
    )
    max_valuation_terminal_growth: Weight | None = Field(
        default=None,
        description="Flag a saved valuation scenario of a held stock whose terminal growth "
        "exceeds this rate (0.03 = 3 %): an unusual assumption to re-check.",
    )
    max_valuation_growth: Weight | None = Field(
        default=None,
        description="Flag a saved valuation scenario of a held stock whose explicit-year free "
        "cash flow growth exceeds this rate in any year.",
    )
    tier_risk_budgets: dict[Bucket, Weight] | None = Field(
        default=None,
        description="Ceiling on each risk tier's share of the portfolio's forecast volatility "
        "(Euler risk contributions of the ex-ante model), by tier bucket in the 'risk' "
        "dimension, e.g. {'core': 0.45, 'growth': 0.45, 'speculative': 0.25}. A tier not listed "
        "is unchecked.",
    )
    min_effective_bets: float | None = Field(
        default=None,
        gt=0,
        le=100,
        description="Floor on the effective number of independent bets (Meucci's entropy of the "
        "principal-component risk shares): a book of many names can still be one bet.",
    )
    tier_speculative_volatility: Weight | None = Field(
        default=None,
        description="A holding in the core or growth tier whose annualised volatility (one year "
        "of daily local-currency returns, as on its page) is above this (0.35 = 35 %) no longer "
        "meets the tier and needs a review. The owner reclassifies by hand.",
    )
    tier_core_max_volatility: Weight | None = Field(
        default=None,
        description="A core-tier holding whose annualised volatility is above this (0.25 = 25 %) "
        "no longer meets the core criterion and needs a review.",
    )
    on_breach: Severity = Field(
        default="severity_2",
        description="Severity of a risk-limit breach (alerted when it opens).",
    )


class StrategyConfig(ConfigModel):
    """The owner's investment strategy.

    Stored as the body of `config_version` rows with domain `strategy`.
    """

    schema_version: Literal[1] = 1

    base_currency: CurrencyCode = Field(
        description="Currency every value is converted to for the overview and allocation."
    )
    fx_source: Literal["cnb", "ecb"] | None = Field(
        default=None,
        description="Reference FX rates. Defaults to ČNB for a CZK base currency, ECB otherwise.",
    )
    weight_basis: Literal["total", "invested"] = Field(
        default="total",
        description="'total' weighs positions against positions plus cash; "
        "'invested' against positions only.",
    )

    goals: str = ""
    horizon_years: int | None = Field(default=None, ge=1, le=100)
    notes: str = Field(
        default="", description="Free-text guidance the AI reads; never evaluated by code."
    )

    categories: list[Dimension] = Field(
        default_factory=lambda: ["sector", "geography", "currency"],
        description="Allocation dimensions. 'currency' is derived from the instrument "
        "unless a classification overrides it.",
    )
    classifications: dict[Ticker, dict[Dimension, Bucket]] = Field(
        default_factory=dict,
        description="Bucket per dimension for each holding, e.g. {'ASML_EQ': {'sector': 'Semis'}}.",
    )

    cash_target: TargetBand | None = None
    target_positions: list[PositionTarget] = Field(default_factory=list)
    target_categories: list[CategoryTarget] = Field(default_factory=list)
    rules: list[StrategyRule] = Field(default_factory=list)
    risk: RiskLimits = Field(default_factory=RiskLimits)
    pretrade: PretradeThresholds = Field(default_factory=PretradeThresholds)
    hard_limit_severity: Severity = Field(
        default="severity_3",
        description="Severity of a position above its hard limit (alerted when it opens, "
        "routed by the notification rules).",
    )
    band_breach_severity: Severity = Field(
        default="severity_1",
        description="Severity of a position, category or cash weight outside its target band. "
        "Severity 1 is listed in the next digest only; 2 or 3 also alert when it opens.",
    )

    @property
    def enabled_rules(self) -> list[StrategyRule]:
        """The rules code evaluates: every rule not switched off."""
        return [rule for rule in self.rules if rule.enabled]

    @model_validator(mode="after")
    def _default_fx_source(self) -> Self:
        if self.fx_source is None:
            self.fx_source = "cnb" if self.base_currency == "CZK" else "ecb"
        return self

    @model_validator(mode="after")
    def _check_consistency(self) -> Self:
        require_unique("categories", self.categories)
        require_unique("target_positions ticker", [p.ticker for p in self.target_positions])
        require_unique(
            "target_categories (dimension, bucket)",
            [(c.dimension, c.bucket) for c in self.target_categories],
        )
        require_unique("rules id", [r.id for r in self.rules])

        known = set(self.categories)
        for ticker, buckets in self.classifications.items():
            unknown = set(buckets) - known
            if unknown:
                raise ValueError(
                    f"classifications[{ticker}] uses undeclared dimensions {sorted(unknown)}"
                )
        for target in self.target_categories:
            if target.dimension not in known:
                raise ValueError(
                    f"target_categories uses undeclared dimension {target.dimension!r}"
                )
        for rule in self.rules:
            if isinstance(rule, CategoryWeightRule) and rule.dimension not in known:
                raise ValueError(f"rule {rule.id!r} uses undeclared dimension {rule.dimension!r}")
            if isinstance(rule, CashWeightRule) and self.weight_basis == "invested":
                # Cash has no weight against positions only: the rule could never be checked.
                raise ValueError(
                    f"rule {rule.id!r} is a cash_weight rule, which needs weight_basis 'total'"
                    " (with 'invested' weights exclude cash)"
                )

        position_total = sum(p.target_weight for p in self.target_positions)
        if self.cash_target is not None:
            position_total += self.cash_target.target_weight
        if position_total > 1 + _WEIGHT_TOLERANCE:
            raise ValueError(f"position and cash targets sum to {position_total:.4f}, above 1")
        for dimension in known:
            total = sum(c.target_weight for c in self.target_categories if c.dimension == dimension)
            if total > 1 + _WEIGHT_TOLERANCE:
                raise ValueError(f"targets for dimension {dimension!r} sum to {total:.4f}, above 1")
        return self
