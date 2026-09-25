"""Event rules config: the event taxonomy's severities and the rules that adjust them.

Severity 0 = ignored, 1 = digest, 2 = digest highlight linked to the thesis, 3 = instant alert.
The classification call only labels the event type; the severity always comes from here.
"""

from typing import Annotated, Literal, Self, cast

from pydantic import Field, model_validator

from steward.config.common import ConfigModel, Ticker

EventType = Literal[
    "guidance_change",
    "m_and_a",
    "dividend_cut",
    "earnings_release",
    "management_change",
    "regulatory_legal",
    "capital_allocation",
    "credit_rating",
    "central_bank",
    "macro_surprise",
    "insider_transaction",
    "product_business",
    "sector_competitor",
    "index_change",
    "analyst_rating",
    "other",
]

EVENT_TYPES: tuple[str, ...] = EventType.__args__  # type: ignore[attr-defined]

# Plain-language description of each type, for the classification prompt and the UI.
EVENT_DESCRIPTIONS: dict[str, str] = {
    "guidance_change": "Profit warning or guidance change: guidance cut or raised, "
    "pre-announcement",
    "m_and_a": "M&A: acquirer, target, merger talks confirmed, divestiture",
    "dividend_cut": "Dividend cut or suspension",
    "earnings_release": "Earnings release: quarterly or annual results",
    "management_change": "Management change: CEO or CFO departure or appointment",
    "regulatory_legal": "Regulatory or legal: investigation, fine, ruling, licence loss, lawsuit",
    "capital_allocation": "Capital allocation: buyback, dividend raise, share issuance, debt raise",
    "credit_rating": "Credit rating upgrade or downgrade",
    "central_bank": "Central bank decision: rate change or guidance shift",
    "macro_surprise": "Macro data release: CPI, jobs, GDP beyond expectations",
    "insider_transaction": "Insider transactions: Form 4, managers' transactions",
    "product_business": "Product or business: major contract, launch, recall, outage",
    "sector_competitor": "Event at a named competitor, supplier or customer, or in the sector",
    "index_change": "Index inclusion or exclusion",
    "analyst_rating": "Analyst rating or price target change",
    "other": "Anything else (market wrap, general commentary, periodic filing)",
}

DEFAULT_SEVERITIES: dict[str, int] = {
    "guidance_change": 3,
    "m_and_a": 3,
    "dividend_cut": 3,
    "earnings_release": 2,
    "management_change": 2,
    "regulatory_legal": 2,
    "capital_allocation": 2,
    "credit_rating": 2,
    "central_bank": 2,
    "macro_surprise": 2,
    "insider_transaction": 1,
    "product_business": 1,
    "sector_competitor": 1,
    "index_change": 1,
    "analyst_rating": 0,
    "other": 0,
}

SeverityLevel = Annotated[int, Field(ge=0, le=3)]


class SeverityOverride(ConfigModel):
    ticker: Ticker
    event_type: EventType
    severity: SeverityLevel


class EventRulesConfig(ConfigModel):
    """Stored as `config_version` rows, domain `event_rules`."""

    schema_version: Literal[1] = 1
    severities: dict[EventType, SeverityLevel] = Field(
        default_factory=lambda: dict(DEFAULT_SEVERITIES),  # type: ignore[arg-type]
        description="Severity per event type; a type left out keeps its default.",
    )
    overrides: list[SeverityOverride] = Field(
        default_factory=list, description="Per-holding severity for one event type."
    )
    escalate_when_thesis_linked: list[EventType] = Field(
        default_factory=lambda: cast(list[EventType], ["regulatory_legal"]),
        description="Event types raised one level (max 3) when the item touches a thesis "
        "KPI or exit condition.",
    )
    insider_cluster_count: int = Field(
        default=3,
        ge=2,
        le=50,
        description="Insider transactions for one holding within the window that count as "
        "cluster buying or selling (raised to severity 2).",
    )
    insider_cluster_days: int = Field(default=14, ge=1, le=90)
    recycled_after_days: int = Field(
        default=7,
        ge=1,
        le=365,
        description="An item whose event happened this long before it was published is a "
        "recycled story: shown, never alerted.",
    )
    thesis_breach_severity: SeverityLevel = Field(
        default=3,
        description="Severity of an alert for a KPI breach found by an earnings check "
        "(0: never alert, the digest still lists it).",
    )
    earnings_check_on: list[EventType] = Field(
        default_factory=lambda: cast(list[EventType], ["earnings_release"]),
        description="Confirmed stories of these types queue an earnings thesis check for "
        "the holding, unless one ran in the last 10 days.",
    )

    @model_validator(mode="after")
    def _one_override_per_pair(self) -> Self:
        pairs = [(o.ticker, o.event_type) for o in self.overrides]
        if len(pairs) != len(set(pairs)):
            raise ValueError("more than one override for the same ticker and event type")
        return self

    def severity(
        self,
        event_type: str,
        ticker: str | None,
        *,
        thesis_linked: bool = False,
        insider_cluster: bool = False,
    ) -> int:
        override = next(
            (o for o in self.overrides if o.ticker == ticker and o.event_type == event_type),
            None,
        )
        if override is not None:
            return override.severity
        base = self.severities.get(event_type, DEFAULT_SEVERITIES.get(event_type, 0))  # type: ignore[call-overload]
        if base == 0:
            return 0
        if insider_cluster and event_type == "insider_transaction":
            base = max(base, 2)
        if thesis_linked and event_type in self.escalate_when_thesis_linked:
            base = min(3, base + 1)
        return int(base)
