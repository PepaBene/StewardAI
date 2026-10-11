"""Research profiles: the shapes a deep research run can take (Configuration: Research
profiles). A quick screen and a thorough workup are two profiles, not two code paths."""

from typing import Annotated, Literal, Self, get_args

from pydantic import Field, field_validator, model_validator

from steward.config.common import ConfigModel
from steward.config.jobs import ModelId

Section = Literal[
    "executive_summary",
    "business",
    "financial_trend",
    "moat_risks",
    "valuation",
    "scenarios",
    "catalysts",
    "counter_case",
    "strategy_fit",
    "thesis_kpis",
    "ownership",
    "relationships",
    "risk",
    "liquidity",
    "open_questions",
]
# Written by the model from the gathered sources (and web search, if allowed).
MODEL_SECTIONS: frozenset[str] = frozenset(
    {
        "executive_summary",
        "business",
        "moat_risks",
        "scenarios",
        "catalysts",
        "counter_case",
        "strategy_fit",
        "thesis_kpis",
        "open_questions",
    }
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

Mode = Literal["quick", "standard", "deep_thesis", "earnings_update", "portfolio_macro", "custom"]
WebEmphasis = Literal["internal", "balanced", "web"]
# What a run does with its findings. None of them changes the active thesis: the others attach
# a proposal or a review to the report, and the owner accepts a proposal field by field.
Authoring = Literal["research_only", "draft_thesis", "propose_update", "review_thesis"]
DomainName = Annotated[
    str,
    Field(
        pattern=r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$",
        max_length=100,
    ),
]
CustomId = Annotated[str, Field(pattern=r"^custom-[a-z0-9]+(-[a-z0-9]+)*$", max_length=40)]
# Source kinds a stale limit can be set for (built-in values: steward.research.ledger).
STALE_KINDS: tuple[str, ...] = ("price", "news_item", "search", "observation", "default")
MAX_CUSTOM_SECTIONS = 6
MAX_TEMPLATE_SECTIONS = 20


class ResearchProfile(ConfigModel):
    id: ProfileId
    name: str = Field(min_length=1, max_length=80)
    sections: list[Section] = Field(min_length=1)
    depth: Literal["brief", "standard", "deep"] = Field(
        default="standard",
        description="Sentences per model-written section: brief 3, standard 6, deep 12.",
    )
    model: ModelId = "gpt-6.1-sol"
    effort: Literal["low", "medium", "high", "xhigh", "max"] = "high"
    max_steps: int = Field(
        default=60, ge=1, le=200, description="Model calls plus web searches per run."
    )
    max_searches: int = Field(default=40, ge=0, le=100)
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
    mode: Mode = Field(
        default="standard",
        description="The kind of run; it shapes the research plan (questions, tools, searches).",
    )
    web_emphasis: WebEmphasis = Field(
        default="balanced",
        description="internal: stored records first, a quarter of the search budget; "
        "balanced and web: the full budget.",
    )
    date_horizon_days: int | None = Field(
        default=None,
        ge=7,
        le=3650,
        description="Prefer evidence from this many days back; older sources are named as old.",
    )
    allow_domains: list[DomainName] = Field(
        default_factory=list,
        max_length=20,
        description="Domains the owner trusts, searched in addition to the tier lists. They are "
        "labelled as owner-trusted and never counted as proof.",
    )
    deny_domains: list[DomainName] = Field(
        default_factory=list, max_length=40, description="Never searched, never cited."
    )
    allow_forums: bool = Field(
        default=False,
        description="Let search use forums and social media. Their sentences are labelled.",
    )
    forum_domains: list[DomainName] = Field(
        default_factory=list,
        max_length=40,
        description="Extra hosts treated as forums or social media, added to the built-in list. "
        "Labelled as forum and searched only when forums are allowed.",
    )
    stale_days: dict[str, Annotated[int, Field(ge=1, le=3650)]] = Field(
        default_factory=dict,
        description="Days after which a cited source of a kind counts as stale, overriding the "
        f"built-in limit. Kinds: {', '.join(STALE_KINDS)}; `default` covers any other kind. "
        "Empty = built-in limits.",
    )
    iterate: bool = Field(
        default=False,
        description="After the first pass, search again for sections left thin, corroborate "
        "single-source claims, and stop when the sections have enough or the budget is used.",
    )
    max_rounds: int = Field(default=1, ge=1, le=3, description="Follow-up rounds when iterating.")
    min_sentences: int = Field(
        default=2, ge=1, le=6, description="Cited sentences a section needs to count as answered."
    )
    template_id: ProfileId | None = Field(
        default=None,
        description="A report template. It decides which sections appear, in what order, with "
        "what titles and in which view; the profile decides how the run investigates.",
    )
    authoring: Authoring = "research_only"
    # `authoring` says what a run does with its findings (a thesis proposal or review); this says
    # who writes the research plan: code (always the same for the same inputs) or a cheap model
    # call, validated in code, with the code plan as the fallback.
    planning: Literal["code", "model"] = "code"
    planner_model: ModelId | None = Field(
        default=None, description="The model that writes the plan; unset = the claim verifier's."
    )

    @model_validator(mode="after")
    def _unique_sections(self) -> Self:
        if len(set(self.sections)) != len(self.sections):
            raise ValueError("a section is listed twice")
        overlap = set(self.allow_domains) & set(self.deny_domains)
        if overlap:
            raise ValueError(f"domains both allowed and denied: {sorted(overlap)}")
        unknown = sorted(set(self.stale_days) - set(STALE_KINDS))
        if unknown:
            raise ValueError(f"unknown source kinds in stale_days: {unknown}")
        forum_overlap = set(self.forum_domains) & (set(self.deny_domains) | set(self.allow_domains))
        if forum_overlap:
            raise ValueError(f"forum domains also allowed or denied: {sorted(forum_overlap)}")
        return self


class TemplateSection(ConfigModel):
    id: str = Field(description="A built-in section id, or the id of one of the custom sections.")
    enabled: bool = True
    title: str | None = Field(default=None, min_length=1, max_length=80)
    max_sentences: int | None = Field(default=None, ge=1, le=20)
    detail_only: bool = Field(
        default=False, description="Shown in the detailed view only; the concise view skips it."
    )


class CustomSection(ConfigModel):
    """A section the owner defines: a title and the question it answers. Written by the model
    from the same sources, cited like any other. It has no code-written figures, so it cannot
    stand in for a trusted calculation."""

    id: CustomId
    title: str = Field(min_length=1, max_length=80)
    task: str = Field(
        min_length=10,
        max_length=400,
        description="What the section should answer, in plain words. No links or markup.",
    )

    @field_validator("task", "title")
    @classmethod
    def _plain_text(cls, value: str) -> str:
        lowered = value.lower()
        if any(mark in lowered for mark in ("http:", "https:", "www.", "<", ">", "```", "{{")):
            raise ValueError("write plain words: no links, markup or template syntax")
        return value


class ReportTemplate(ConfigModel):
    """How a report is presented (Phase 3): sections, order, titles, depth per section and the
    default view. It cannot change a code-written figure or the strategy; it only arranges."""

    schema_version: Literal[1] = 1
    id: ProfileId
    name: str = Field(min_length=1, max_length=80)
    sections: list[TemplateSection] = Field(min_length=1, max_length=MAX_TEMPLATE_SECTIONS)
    custom_sections: list[CustomSection] = Field(
        default_factory=list, max_length=MAX_CUSTOM_SECTIONS
    )
    default_view: Literal["concise", "detailed"] = "detailed"

    @model_validator(mode="after")
    def _known_sections(self) -> Self:
        custom = [c.id for c in self.custom_sections]
        builtin = set(get_args(Section))
        if len(set(custom)) != len(custom):
            raise ValueError("a custom section id is used twice")
        ids = [s.id for s in self.sections]
        if len(set(ids)) != len(ids):
            raise ValueError("a section is listed twice")
        unknown = [i for i in ids if i not in builtin and i not in custom]
        if unknown:
            raise ValueError(f"unknown sections: {unknown}")
        unused = [c for c in custom if c not in ids]
        if unused:
            raise ValueError(f"custom sections defined but not placed: {unused}")
        if not any(s.enabled for s in self.sections):
            raise ValueError("enable at least one section")
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
            model="gpt-6.1-sol",
            effort="medium",
            max_steps=20,
            max_searches=10,
            include_peers=False,
            valuation_methods=["pe_ttm"],
            mode="quick",
            web_emphasis="internal",
        ),
        ResearchProfile(
            id="deep-thesis",
            name="Deep thesis workup",
            sections=[
                "executive_summary",
                "business",
                "financial_trend",
                "moat_risks",
                "valuation",
                "scenarios",
                "catalysts",
                "counter_case",
                "strategy_fit",
                "thesis_kpis",
                "open_questions",
            ],
            depth="deep",
            max_steps=100,
            max_searches=60,
            mode="deep_thesis",
            iterate=True,
            max_rounds=2,
            template_id="thesis-memo",
            authoring="draft_thesis",
        ),
        ResearchProfile(
            id="earnings-update",
            name="Earnings and thesis update",
            sections=[
                "executive_summary",
                "financial_trend",
                "thesis_kpis",
                "counter_case",
                "strategy_fit",
                "open_questions",
            ],
            max_steps=40,
            max_searches=20,
            mode="earnings_update",
            date_horizon_days=120,
            iterate=True,
            template_id="update-note",
            authoring="propose_update",
        ),
    ]


def _default_templates() -> list[ReportTemplate]:
    def sec(*ids: str, detail_only: tuple[str, ...] = ()) -> list[TemplateSection]:
        return [TemplateSection(id=i, detail_only=i in detail_only) for i in ids]

    return [
        ReportTemplate(
            id="thesis-memo",
            name="Thesis memo",
            sections=sec(
                "executive_summary",
                "business",
                "financial_trend",
                "valuation",
                "scenarios",
                "catalysts",
                "moat_risks",
                "counter_case",
                "strategy_fit",
                "thesis_kpis",
                "open_questions",
                detail_only=("financial_trend", "moat_risks", "catalysts"),
            ),
            default_view="concise",
        ),
        ReportTemplate(
            id="update-note",
            name="Update note",
            sections=sec(
                "executive_summary",
                "financial_trend",
                "thesis_kpis",
                "counter_case",
                "strategy_fit",
                "open_questions",
            ),
            default_view="concise",
        ),
    ]


class ResearchProfilesConfig(ConfigModel):
    """Stored as `config_version` rows, domain `research_profiles`."""

    schema_version: Literal[1] = 1
    profiles: list[ResearchProfile] = Field(default_factory=_defaults, min_length=1)
    default_profile: ProfileId = "standard"
    templates: list[ReportTemplate] = Field(
        default_factory=_default_templates,
        max_length=20,
        description="Report templates a profile can name. Kept apart from the profiles: "
        "a profile is how to investigate, a template is how to present.",
    )

    @model_validator(mode="after")
    def _known_default(self) -> Self:
        ids = [p.id for p in self.profiles]
        if len(set(ids)) != len(ids):
            raise ValueError("profile ids must be unique")
        if self.default_profile not in ids:
            raise ValueError(f"default_profile {self.default_profile!r} is not a profile id")
        template_ids = [t.id for t in self.templates]
        if len(set(template_ids)) != len(template_ids):
            raise ValueError("template ids must be unique")
        for p in self.profiles:
            if p.template_id and p.template_id not in template_ids:
                raise ValueError(f"profile {p.id!r} names an unknown template {p.template_id!r}")
        return self

    def template_for(self, profile: ResearchProfile) -> ReportTemplate | None:
        return next((t for t in self.templates if t.id == profile.template_id), None)

    def get(self, profile_id: str | None) -> ResearchProfile | None:
        wanted = profile_id or self.default_profile
        return next((p for p in self.profiles if p.id == wanted), None)
