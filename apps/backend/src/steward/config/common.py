"""Field types and helpers shared across config domains."""

from collections import Counter
from collections.abc import Hashable, Iterable
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

# Trading212 instrument tickers, e.g. "AAPL_US_EQ" or "RRl_EQ" (case matters).
Ticker = Annotated[str, Field(pattern=r"^[A-Za-z0-9._-]{1,32}$", examples=["AAPL_US_EQ"])]

# Stable identifiers referenced from other records (verdicts cite KPI ids, actions cite rule ids).
Slug = Annotated[
    str, Field(pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$", max_length=64, examples=["max-single-position"])
]

CurrencyCode = Annotated[str, Field(pattern=r"^[A-Z]{3}$", examples=["CZK"])]

# Portfolio weights are fractions: 0.10 means 10 %.
Weight = Annotated[float, Field(ge=0, le=1)]

ComparisonOperator = Literal["<", "<=", ">", ">=", "=="]

Severity = Literal["severity_1", "severity_2", "severity_3"]


class ConfigModel(BaseModel):
    """Base for config bodies: unknown keys are errors, so typos fail at save time."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def require_unique(label: str, values: Iterable[Hashable]) -> None:
    duplicates = [value for value, count in Counter(values).items() if count > 1]
    if duplicates:
        raise ValueError(f"duplicate {label}: {sorted(map(str, duplicates))}")
