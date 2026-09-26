"""Research profiles: the shapes a deep research run can take (Configuration: Research
profiles). A quick screen and a thorough workup are two profiles, not two code paths."""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from steward.config.common import ConfigModel
from steward.config.jobs import ModelId

Section = Literal[
    "business",
    "financial_trend",
    "moat_risks",
    "valuation",
    "strategy_fit",
    "ownership",
    "relationships",
    "risk",
    "liquidity",
    "open_questions",
]
# Written by the model from the gathered sources (and web search, if allowed).
MODEL_SECTIONS: frozenset[str] = frozenset(
    {"business", "moat_risks", "strategy_fit", "open_questions"}
)
# Written by code from stored records; every figure cites the record it comes from.
CODE_SECTIONS: frozenset[str] = frozenset(
    {"financial_trend", "valuation", "ownership", "relationships", "risk", "liquidity"}
)
ValuationMethod = Literal["pe_ttm", "earnings_yield", "dividend_yield_ttm"]
ALL_VALUATION_METHODS: tuple[ValuationMethod, ...] = (
    "pe_ttm",
    "earnings_yield",
    "dividend_yield_ttm",
)
Tier = Literal[1, 2, 3]
DEFAULT_TIERS: tuple[Tier, ...] = (1, 2)
ProfileId = Annotated[str, Field(pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$", max_length=40)]

SENTENCES_BY_DEPTH = {"brief": 3, "standard": 6, "deep": 12}


class ResearchProfile(ConfigModel):
    id: ProfileId
    name: str = Field(min_length=1, max_length=80)
    sections: list[Section] = Field(min_length=1)
    depth: Literal["brief", "standard", "deep"] = Field(
        default="standard",
        description="Sentences per model-written section: brief 3, standard 6, deep 12.",
    )
    model: ModelId = "claude-opus-5-5"
    effort: Literal["low", "medium", "high", "xhigh", "max"] = "high"
    max_steps: int = Field(
        default=20, ge=1, le=60, description="Claude calls plus web searches per run."
    )
    max_searches: int = Field(default=15, ge=0, le=40)
    source_tiers: list[Tier] = Field(
        default_factory=lambda: list(DEFAULT_TIERS),
        min_length=1,
        description="Outlet tiers web search may use and the report may cite.",
    )
    include_peers: bool = Field(
        default=True, description="Name the holding's competitors and compare with them."
    )
    valuation_methods: list[ValuationMethod] = Field(
        default_factory=lambda: list(ALL_VALUATION_METHODS)
    )
    language: Literal["en", "cs"] = "en"
    top_holders: int = Field(
        default=3,
        ge=1,
        le=20,
        description="Largest 13F holders named in the ownership section.",
    )

    @model_validator(mode="after")
    def _unique_sections(self) -> Self:
        if len(set(self.sections)) != len(self.sections):
            raise ValueError("a section is listed twice")
        return self


def _defaults() -> list[ResearchProfile]:
    return [
        ResearchProfile(
            id="standard",
            name="Standard workup",
            sections=[
                "business",
                "financial_trend",
                "moat_risks",
                "valuation",
                "ownership",
                "relationships",
                "risk",
                "liquidity",
                "strategy_fit",
                "open_questions",
            ],
        ),
        ResearchProfile(
            id="quick",
            name="Quick screen",
            sections=["business", "financial_trend", "valuation", "strategy_fit"],
            depth="brief",
            model="claude-sonnet-5",
            effort="medium",
            max_steps=6,
            max_searches=3,
            include_peers=False,
            valuation_methods=["pe_ttm"],
        ),
    ]


class ResearchProfilesConfig(ConfigModel):
    """Stored as `config_version` rows, domain `research_profiles`."""

    schema_version: Literal[1] = 1
    profiles: list[ResearchProfile] = Field(default_factory=_defaults, min_length=1)
    default_profile: ProfileId = "standard"

    @model_validator(mode="after")
    def _known_default(self) -> Self:
        ids = [p.id for p in self.profiles]
        if len(set(ids)) != len(ids):
            raise ValueError("profile ids must be unique")
        if self.default_profile not in ids:
            raise ValueError(f"default_profile {self.default_profile!r} is not a profile id")
        return self

    def get(self, profile_id: str | None) -> ResearchProfile | None:
        wanted = profile_id or self.default_profile
        return next((p for p in self.profiles if p.id == wanted), None)
