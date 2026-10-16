# ruff: noqa: E501 - rationale text reads better unbroken
"""Curated starter screens (insight plan I10.7): about a dozen screen definitions with a title and
a one-paragraph rationale, kept as a versioned config document the owner can edit. They are
starting points written in the screener's own metrics, not recommendations."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import Field, field_validator, model_validator

from steward.config.common import ConfigModel


class TemplateFilter(ConfigModel):
    metric: str
    op: Literal["gte", "lte", "in"]
    value: Decimal | list[str]


class ScreenTemplate(ConfigModel):
    id: str = Field(pattern=r"^[a-z0-9_]{2,40}$")
    title: str = Field(min_length=2, max_length=80)
    rationale: str = Field(min_length=10, max_length=800)
    filters: list[TemplateFilter] = Field(min_length=1, max_length=12)
    sort_by: str | None = None
    descending: bool = True

    @model_validator(mode="after")
    def _known_metrics(self) -> ScreenTemplate:
        from steward.discovery.metrics import STOCK_CATALOGUE

        for f in self.filters:
            m = STOCK_CATALOGUE.get(f.metric)
            if m is None:
                raise ValueError(f"{self.id}: unknown metric {f.metric}")
            if (m.kind == "numeric") != (f.op != "in"):
                raise ValueError(f"{self.id}: operator {f.op} does not suit {f.metric}")
        if self.sort_by is not None and self.sort_by not in STOCK_CATALOGUE:
            raise ValueError(f"{self.id}: unknown sort metric {self.sort_by}")
        return self


def _f(metric: str, op: Literal["gte", "lte", "in"], value: str | list[str]) -> TemplateFilter:
    return TemplateFilter(
        metric=metric, op=op, value=Decimal(value) if isinstance(value, str) else value
    )


def default_templates() -> list[ScreenTemplate]:
    return [
        ScreenTemplate(
            id="quality_payers",
            title="Quality dividend payers",
            rationale="Companies that pay a meaningful dividend and pass most of the dividend checks: a payout covered by earnings and cash flow, and a record of paying. A dividend that is well covered is less likely to be cut when profits dip.",
            filters=[
                _f("dividend_yield", "gte", "0.025"),
                _f("checks_dividend_score", "gte", "4"),
                _f("payout_ratio", "lte", "0.75"),
            ],
            sort_by="dividend_yield",
        ),
        ScreenTemplate(
            id="undervalued_insider_buying",
            title="Undervalued with insider buying",
            rationale="Stocks that pass most value checks while insiders bought on the open market in the last 90 days. Insiders buy for one reason; their purchases are not a forecast, but they show where the people closest to the business put their own money.",
            filters=[_f("checks_value_score", "gte", "4"), _f("insider_buyers_90d", "gte", "1")],
            sort_by="insider_buyers_90d",
        ),
        ScreenTemplate(
            id="healthy_rising_margins",
            title="Financially healthy, rising sales",
            rationale="Balance sheets that pass most health checks and revenue that grew over the last year, with an operating margin above 10 %. Growth paid for by a strong balance sheet needs no new capital.",
            filters=[
                _f("checks_health_score", "gte", "4"),
                _f("revenue_growth", "gte", "0"),
                _f("operating_margin", "gte", "0.1"),
            ],
            sort_by="revenue_growth",
        ),
        ScreenTemplate(
            id="compounders",
            title="High returns on capital",
            rationale="Businesses earning over 15 % on invested capital with gross margins above 40 % and little net debt. High returns that persist usually point to a durable advantage; the margin and leverage limits keep out one-off years and borrowed returns.",
            filters=[
                _f("roic", "gte", "0.15"),
                _f("gross_margin", "gte", "0.4"),
                _f("net_debt_to_equity", "lte", "0.5"),
            ],
            sort_by="roic",
        ),
        ScreenTemplate(
            id="deep_value",
            title="Deep value",
            rationale="Low earnings and book multiples with positive free cash flow. Cheap stocks are often cheap for a reason, so the cash-flow floor screens out companies whose earnings are not turning into cash.",
            filters=[
                _f("pe_ratio", "lte", "10"),
                _f("price_to_book", "lte", "1.2"),
                _f("fcf_yield", "gte", "0.05"),
            ],
            sort_by="fcf_yield",
        ),
        ScreenTemplate(
            id="cash_returners",
            title="Cash returners",
            rationale="Companies handing back at least 6 % of their market value a year through dividends and buybacks together, without raising debt to do it. Total shareholder yield counts both routes, so a company that prefers buybacks is not missed.",
            filters=[
                _f("shareholder_yield", "gte", "0.06"),
                _f("net_debt_to_ebitda", "lte", "2.5"),
            ],
            sort_by="shareholder_yield",
        ),
        ScreenTemplate(
            id="dividend_growers",
            title="Dividend growers",
            rationale="Dividends per share up at least 5 % a year over five years with a payout under 70 % of earnings. Steady growth with room in the payout is the pattern of a dividend that can keep rising.",
            filters=[_f("dividend_growth_5y", "gte", "0.05"), _f("payout_ratio", "lte", "0.7")],
            sort_by="dividend_growth_5y",
        ),
        ScreenTemplate(
            id="garp",
            title="Growth at a reasonable price",
            rationale="Revenue growing over 10 % a year at a P/E under 20 and a positive net margin. It looks for growth the market has not priced at a premium yet.",
            filters=[
                _f("revenue_growth", "gte", "0.1"),
                _f("pe_ratio", "lte", "20"),
                _f("net_margin", "gte", "0"),
            ],
            sort_by="revenue_growth",
        ),
        ScreenTemplate(
            id="momentum_quality",
            title="Momentum with quality",
            rationale="Stocks in the top half of 12-1 month momentum that also pass most past-performance checks. Momentum alone reverses sharply; requiring a record of earnings keeps out pure speculation.",
            filters=[_f("momentum_12_1", "gte", "0.1"), _f("checks_past_score", "gte", "4")],
            sort_by="momentum_12_1",
        ),
        ScreenTemplate(
            id="low_volatility_income",
            title="Low-volatility income",
            rationale="Yields above 3 % with one-year volatility under 20 %. For income that does not come with large swings in the capital behind it.",
            filters=[_f("dividend_yield", "gte", "0.03"), _f("volatility_1y", "lte", "0.2")],
            sort_by="volatility_1y",
            descending=False,
        ),
        ScreenTemplate(
            id="shrinking_share_count",
            title="Shrinking share count",
            rationale="Companies whose diluted share count fell over the last year and that pay at least 2 % of their value in buybacks. Each remaining share owns more of the business; the buyback-yield floor separates real repurchases from rounding.",
            filters=[_f("share_change_1y", "lte", "0"), _f("buyback_yield", "gte", "0.02")],
            sort_by="buyback_yield",
        ),
        ScreenTemplate(
            id="clean_balance_sheet",
            title="Clean balance sheets, no red flags",
            rationale="Net cash or little net debt, no failed risk checks, and cash conversion above 80 %. A starting list of companies where the accounts give little to worry about.",
            filters=[
                _f("net_debt_to_equity", "lte", "0.2"),
                _f("checks_risk_flags", "lte", "0"),
                _f("free_cash_flow_conversion", "gte", "0.8"),
            ],
            sort_by="free_cash_flow_conversion",
        ),
    ]


class ScreenTemplatesConfig(ConfigModel):
    schema_version: Literal[1] = 1
    templates: list[ScreenTemplate] = Field(default_factory=default_templates, max_length=40)

    @field_validator("templates")
    @classmethod
    def _unique(cls, v: list[ScreenTemplate]) -> list[ScreenTemplate]:
        ids = [t.id for t in v]
        if len(set(ids)) != len(ids):
            raise ValueError("template ids must be unique")
        return v
