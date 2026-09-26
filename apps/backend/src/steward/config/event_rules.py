"""Event rules config: the event taxonomy's severities and the rules that adjust them.

Severity 0 = ignored, 1 = digest, 2 = digest highlight linked to the thesis, 3 = instant alert.
The classification call only labels the event type; the severity always comes from here.
"""

import re
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
    "delisting",
    "entity_status",
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
    "delisting": "Delisting: trading on the home market ends (regulator reference data)",
    "entity_status": "Legal entity status change: entity inactive, LEI lapsed or retired",
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
    "delisting": 2,
    "entity_status": 1,
    "other": 0,
}

SeverityLevel = Annotated[int, Field(ge=0, le=3)]


class SeverityOverride(ConfigModel):
    ticker: Ticker
    event_type: EventType
    severity: SeverityLevel


class AnnouncementRule(ConfigModel):
    """Maps a regulatory announcement's type (the RNS headline or the EQS category) to an
    event type. First match wins; `classify` leaves the item to the classifier."""

    service: Literal["rns", "eqs", "any"] = "any"
    pattern: str = Field(
        min_length=1,
        max_length=200,
        description="Case-insensitive regular expression matched against the announcement "
        "type (RNS: the headline, e.g. 'Final Results'; EQS: the category, e.g. 'Ad-hoc').",
    )
    event_type: EventType | Literal["classify"]

    @model_validator(mode="after")
    def _valid_pattern(self) -> Self:
        try:
            re.compile(self.pattern)
        except re.error as exc:
            raise ValueError(f"invalid pattern {self.pattern!r}: {exc}") from exc
        return self


def _default_announcement_rules() -> list[AnnouncementRule]:
    rules: list[tuple[str, str, str]] = [
        ("any", r"profit warning", "guidance_change"),
        ("rns", r"(final|interim|half[- ]?year|preliminary|q[1-4]|quarterly|full[- ]year) "
         r"results|results for the|annual financial report|half[- ]year report",
         "earnings_release"),
        ("rns", r"trading (statement|update)|q[1-4] trading", "earnings_release"),
        ("rns", r"director/pdmr|pdmr shareholding|director.?s? dealing", "insider_transaction"),
        ("rns", r"offer for|possible offer|recommended (cash )?offer|rule 2\.[47]|scheme of "
         r"arrangement|merger|acquisition|disposal", "m_and_a"),
        ("rns", r"transaction in own shares|buyback|buy-back|dividend declaration|"
         r"scrip dividend|capital raise|placing", "capital_allocation"),
        ("rns", r"directorate change|board change|appointment of|ceo|chief executive|"
         r"chief financial", "management_change"),
        ("rns", r"holding\(s\) in company|total voting rights|notice of agm|result of agm|"
         r"block listing|form 8", "other"),
        ("eqs", r"ad-?hoc", "classify"),
        ("eqs", r"directors.? dealings", "insider_transaction"),
        ("eqs", r"voting rights", "other"),
        ("eqs", r"financial reports?|half-?year report|quarterly (report|statement)|"
         r"annual report", "earnings_release"),
        ("any", r".", "classify"),
    ]  # fmt: skip
    return [
        AnnouncementRule.model_validate({"service": s, "pattern": p, "event_type": e})
        for s, p, e in rules
    ]


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
    price_move_threshold_pct: float = Field(
        default=8.0,
        gt=0,
        le=100,
        description="A daily close-to-close move beyond +-this percentage queues an event "
        "escalation for the holding.",
    )
    macro_surprise_bands: dict[
        Annotated[str, Field(pattern=r"^[a-z0-9_]{1,40}$")], Annotated[float, Field(ge=0)]
    ] = Field(
        default_factory=lambda: {
            "hicp": 0.3,
            "unemployment": 0.3,
            "gdp": 0.5,
            "industrial_production": 2.0,
            "sentiment": 3.0,
            "us_cpi": 0.3,
            "us_unemployment": 0.2,
            "ecb_deposit_rate": 0.0,
            "fed_funds": 0.0,
        },
        description="Per macro series key: a new release that moves more than this against "
        "the previous one is a macro surprise (in the series' own unit; 0: any change, e.g. "
        "a policy rate). Evaluated only on release days.",
    )
    announcement_types: list[AnnouncementRule] = Field(
        default_factory=_default_announcement_rules,
        description="How RNS and EQS announcement types map to event types.",
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

    def announcement_event(self, service: str, announcement_type: str) -> str | None:
        """The event type for an announcement, or None to leave it to the classifier."""
        for rule in self.announcement_types:
            if rule.service not in ("any", service):
                continue
            if re.search(rule.pattern, announcement_type, re.IGNORECASE):
                return None if rule.event_type == "classify" else rule.event_type
        return None

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
