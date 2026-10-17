"""Verification config: the thresholds of the defence layers (Defence against wrong
information): sanity bounds and tolerances for extracted figures, the corroboration rules
per tier, source trust from owner feedback, and the claim verifier's strictness."""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from steward.config.common import ConfigModel
from steward.config.jobs import EFFORT_HELP, Effort, ModelId, check_effort

Metric = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")]

ClaimTarget = Literal[
    "daily_digest", "weekly_digest", "monthly_digest", "quarterly_digest", "escalation"
]
ALL_CLAIM_TARGETS: tuple[ClaimTarget, ...] = (
    "daily_digest",
    "weekly_digest",
    "monthly_digest",
    "quarterly_digest",
    "escalation",
)


class CorroborationRules(ConfigModel):
    """When a news story counts as confirmed (layer 2)."""

    tier1_confirms_alone: bool = Field(
        default=True,
        description="A tier-1 source (the company, a regulator, a statistics office) confirms "
        "a story on its own. Off: it counts as one independent source like a tier-2 outlet.",
    )
    independent_sources_to_confirm: int = Field(
        default=2,
        ge=2,
        le=5,
        description="Independent tier-2 outlets (different stories, not copies of one wire "
        "story) needed to confirm a story.",
    )


class TrustSettings(ConfigModel):
    """Source trust (layer 1): owner feedback moves a source's score between 0 and 1."""

    wrong_penalty: float = Field(
        default=0.2, ge=0, le=1, description="Score lost each time the owner marks it wrong."
    )
    useful_reward: float = Field(
        default=0.05, ge=0, le=1, description="Score gained each time the owner marks it useful."
    )
    demote_below: float = Field(
        default=0.6,
        ge=0,
        le=1,
        description="A source scoring below this counts one tier lower in corroboration "
        "(tier 1 as tier 2, tier 2 as tier 3).",
    )


class ClaimVerifierSettings(ConfigModel):
    """The claim verifier (layer 4): every model-written sentence is checked against the
    snippets it cites before a report is stored or sent."""

    enabled: bool = True
    model: ModelId = "gpt-6-luna"
    effort: Effort | None = Field(default=None, description=EFFORT_HELP)
    mode: Literal["strip", "mark"] = Field(
        default="strip",
        description="strip: remove an unsupported sentence; mark: keep it, marked unsupported.",
    )
    numeric_check: bool = Field(
        default=True,
        description="Before the model call, code checks that every number in a sentence "
        "appears in a snippet it cites; a sentence that fails is unsupported without a call.",
    )
    date_check: bool = Field(
        default=True,
        description="Code checks that every full date in a sentence (2026-11-12, 12 November "
        "2026, November 12, 2026) appears in a snippet it cites; a sentence that fails is "
        "unsupported without a model call.",
    )
    quote_check: bool = Field(
        default=True,
        description="A sentence that is a verbatim run of a cited snippet (and passed the "
        "number and date checks) is supported without a model call.",
    )
    applies_to: list[ClaimTarget] = Field(
        default_factory=lambda: list(ALL_CLAIM_TARGETS),
        description="Reports whose sentences are checked. Alerts are composed in code from "
        "the source's own headline, so they have no model-written sentences to check.",
    )

    @model_validator(mode="after")
    def _effort_supported(self) -> Self:
        check_effort(self.model, self.effort, "effort")
        return self


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

    corroboration: CorroborationRules = Field(default_factory=CorroborationRules)
    trust: TrustSettings = Field(default_factory=TrustSettings)
    claim_verifier: ClaimVerifierSettings = Field(default_factory=ClaimVerifierSettings)

    def sanity_band(self, metric: str) -> float | None:
        if metric in self.sanity_overrides:
            return self.sanity_overrides[metric]
        return self.sanity_max_change_qoq
