"""Verification config: the thresholds the validator applies to extracted figures."""

from typing import Annotated, Literal

from pydantic import Field

from steward.config.common import ConfigModel

Metric = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")]


class VerificationConfig(ConfigModel):
    """Stored as `config_version` rows, domain `verification`."""

    schema_version: Literal[1] = 1
    sanity_max_change_qoq: float | None = Field(
        default=0.5,
        gt=0,
        description="A figure that moves more than this fraction against the previous "
        "quarter's verified figure is held for review (0.5 = ±50 %). Null disables.",
    )
    sanity_overrides: dict[Metric, float | None] = Field(
        default_factory=dict,
        description="Per-metric band replacing the default; null disables the bound for "
        "that metric (e.g. seasonal or often sign-changing figures).",
    )
    stated_ratio_tolerance_pp: float = Field(
        default=0.15,
        ge=0,
        description="How far (in percentage points) a ratio the document states may differ "
        "from the one recomputed from its raw figures before the stated one is rejected.",
    )
    period_end_tolerance_days: int = Field(
        default=10,
        ge=0,
        le=31,
        description="Slack between a stated period end and the calendar month end expected "
        "from the fiscal year end (52/53-week fiscal years end on a weekday, not month end).",
    )
    max_report_lag_days: int = Field(
        default=150,
        ge=30,
        le=400,
        description="Latest a release may come out after the end of the period it reports.",
    )

    def sanity_band(self, metric: str) -> float | None:
        if metric in self.sanity_overrides:
            return self.sanity_overrides[metric]
        return self.sanity_max_change_qoq
