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


class RiskLimits(ConfigModel):
    """Portfolio risk limits, checked after each sync. Every limit is optional (null = off).

    A breach here is a prompt for review, not an instruction to trade: the strategy decides
    buys and sells on fundamentals and news, so a price-based limit only forces a thesis review.
    """

    max_volatility: Weight | None = Field(
        default=None,
        description="Ceiling on the portfolio's annualised volatility (0.20 = 20 %), measured "
        "on the time-weighted daily returns over the last year.",
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
