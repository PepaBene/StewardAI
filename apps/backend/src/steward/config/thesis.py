"""Thesis config: why each holding is owned, the KPIs that must hold, and what would end it."""

import datetime as dt
from typing import Literal, Self

from pydantic import Field, model_validator

from steward.config.common import ComparisonOperator, ConfigModel, Slug, Ticker, require_unique


class ThesisKPI(ConfigModel):
    """An absolute threshold the owner sets. v1 has no consensus-estimate comparisons."""

    id: Slug
    metric: str = Field(
        pattern=r"^[a-z][a-z0-9_]{0,63}$",
        examples=["revenue_growth_yoy"],
        description="Metric name the extraction job looks for.",
    )
    operator: ComparisonOperator
    threshold: float
    unit: str = Field(min_length=1, max_length=32, examples=["%", "EUR bn"])
    basis: Literal["reported", "adjusted"] = "reported"
    period: Literal["quarter", "annual", "ttm"] = "quarter"
    description: str = ""


class ThesisConfig(ConfigModel):
    ticker: Ticker
    why_owned: str = Field(min_length=1)
    kpis: list[ThesisKPI] = Field(default_factory=list)
    exit_conditions: list[str] = Field(
        default_factory=list, description="Conditions that would end the thesis, in plain words."
    )
    review_date: dt.date | None = None

    @model_validator(mode="after")
    def _unique_kpi_ids(self) -> Self:
        require_unique(f"kpi id in thesis {self.ticker}", [k.id for k in self.kpis])
        return self


class ThesesConfig(ConfigModel):
    """All theses. Stored as the body of `config_version` rows, domain `theses`."""

    schema_version: Literal[1] = 1
    theses: list[ThesisConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def _one_thesis_per_ticker(self) -> Self:
        require_unique("thesis ticker", [t.ticker for t in self.theses])
        return self
