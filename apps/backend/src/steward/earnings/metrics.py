"""Metric names: what extraction asks for, and how derived KPIs are recomputed in code.

Extraction only ever asks the model for figures printed in the document. Ratios and growth
rates a thesis KPI needs are computed here from those raw figures, so the model's own
arithmetic never reaches a verdict:

- `<base>_growth_yoy`: the period against the same period one fiscal year earlier;
- `<base>_growth_qoq`: a quarter against the previous quarter;
- margins (`gross_margin`, `operating_margin`, ...): numerator over revenue.

Any other metric name in a thesis is extracted as printed (e.g. `backlog`,
`net_revenue_retention`), described to the model by the KPI's description.
"""

from dataclasses import dataclass

# Standard figures every earnings check extracts, with the definition the model gets.
BASE_METRICS: dict[str, str] = {
    "revenue": "Total revenue / net sales / turnover for the period (not a segment).",
    "gross_profit": "Gross profit. Some companies label it 'gross margin' in currency "
    "(e.g. Apple's 'Gross margin' line in USD millions) - that is gross_profit.",
    "operating_income": "Operating income / operating profit / income from operations "
    "(EBIT as the company reports it on the income statement).",
    "net_income": "Net income attributable to the parent company's shareholders "
    "(total net income if no split is given).",
    "eps_diluted": "Diluted earnings per share.",
}

# Extracted only when a thesis asks for them.
OPTIONAL_BASE_METRICS: dict[str, str] = {
    "free_cash_flow": "Free cash flow exactly as the company states it.",
    "operating_cash_flow": "Net cash provided by operating activities.",
    "ebitda": "EBITDA exactly as the company states it.",
    "backlog": "Order backlog at period end.",
    "bookings": "Net bookings / order intake for the period.",
}

MARGINS: dict[str, str] = {
    "gross_margin": "gross_profit",
    "operating_margin": "operating_income",
    "net_margin": "net_income",
    "fcf_margin": "free_cash_flow",
    "ebitda_margin": "ebitda",
}

# Figures that add up across quarters (used for trailing-twelve-month values).
ADDITIVE = {
    "revenue", "gross_profit", "operating_income", "net_income", "eps_diluted",
    "free_cash_flow", "operating_cash_flow", "ebitda", "bookings",
}  # fmt: skip

GROWTH_SUFFIXES = ("_growth_yoy", "_growth_qoq")


@dataclass(frozen=True)
class Derivation:
    kind: str  # "direct", "margin", "growth_yoy", "growth_qoq"
    base: str  # the extracted metric (numerator for margins)
    denominator: str | None = None

    @property
    def components(self) -> tuple[str, ...]:
        return (self.base,) if self.denominator is None else (self.base, self.denominator)


def derivation(metric: str) -> Derivation:
    for suffix in GROWTH_SUFFIXES:
        if metric.endswith(suffix) and len(metric) > len(suffix):
            base = metric.removesuffix(suffix)
            inner = derivation(base)
            if inner.kind != "direct":
                # Growth of a ratio is not defined here; treat the name as printed.
                return Derivation("direct", metric)
            return Derivation(suffix.lstrip("_"), base)
    if metric in MARGINS:
        return Derivation("margin", MARGINS[metric], "revenue")
    return Derivation("direct", metric)


def metrics_to_extract(kpi_metrics: list[str]) -> list[str]:
    """Base metrics to ask for: the standard set plus everything the KPIs need."""
    wanted = list(BASE_METRICS)
    for metric in kpi_metrics:
        for component in derivation(metric).components:
            if component not in wanted:
                wanted.append(component)
        # A ratio the document states itself is extracted too, to cross-check it.
        if derivation(metric).kind != "direct" and metric not in wanted:
            wanted.append(metric)
    return wanted


def describe(metric: str, kpi_descriptions: dict[str, str]) -> str:
    if metric in BASE_METRICS:
        return BASE_METRICS[metric]
    if metric in OPTIONAL_BASE_METRICS:
        return OPTIONAL_BASE_METRICS[metric]
    d = derivation(metric)
    if d.kind == "margin":
        return f"{metric.replace('_', ' ')} as a percentage, only if the document states it."
    if d.kind.startswith("growth"):
        span = "year over year" if d.kind == "growth_yoy" else "quarter over quarter"
        return f"{d.base.replace('_', ' ')} growth {span} in percent, only if stated."
    return kpi_descriptions.get(metric) or metric.replace("_", " ")
