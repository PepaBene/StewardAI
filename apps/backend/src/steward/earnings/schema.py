"""The JSON the extraction call must return (Claude structured output), one object per
figure. Everything here is a claim until the validator has checked it against the
archived document."""

from typing import Literal

from pydantic import BaseModel, Field

from steward.earnings.periods import FiscalPeriod
from steward.earnings.units import Scale, UnitKind


class ExtractedFigure(BaseModel):
    metric: str = Field(description="One of the requested metric names, exactly.")
    basis: Literal["reported", "adjusted"] = Field(
        description="reported = GAAP/IFRS statement figure; adjusted = non-GAAP/alternative "
        "performance measure the company labels as such."
    )
    fiscal_year: int = Field(description="The company's own fiscal year label.")
    fiscal_period: FiscalPeriod
    period_end: str | None = Field(
        description="Period end date as the document states it, YYYY-MM-DD, or null."
    )
    value_as_written: str = Field(
        description="The number exactly as printed, e.g. '94,036', '(171)', '7.52'. No "
        "currency symbol or unit word."
    )
    value: float = Field(description="Numeric reading of value_as_written, in `scale`.")
    scale: Scale
    unit: UnitKind
    currency: str | None = Field(description="ISO 4217 code for money and per-share figures.")
    source_snippet: str = Field(
        description="The verbatim sentence or table row containing value_as_written."
    )
    unit_snippet: str | None = Field(
        description="Verbatim text stating the scale/currency (e.g. a table's '(In millions, "
        "except per-share amounts)' line) when source_snippet does not state it itself."
    )


class ExtractionResult(BaseModel):
    document_kind: Literal[
        "earnings_release", "interim_report", "annual_report", "presentation", "other"
    ]
    company_name: str | None
    reported_fiscal_year: int | None = Field(description="Fiscal year of the main period.")
    reported_fiscal_period: FiscalPeriod | None
    figures: list[ExtractedFigure]
    not_found: list[str] = Field(description="Requested metrics the document does not state.")
