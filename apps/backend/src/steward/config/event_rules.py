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
    "credit_stress",
    "trade_exposure",
    "patent_status",
    "other",
]

EVENT_TYPES: tuple[str, ...] = EventType.__args__  # type: ignore[attr-defined]
# Raised by code from stored observations, never by the news classifier: they stay out of the
# classification prompt, so adding one does not change the model-facing text.
SYSTEM_RAISED_EVENT_TYPES: frozenset[str] = frozenset(
    {"credit_stress", "trade_exposure", "patent_status"}
)
CLASSIFIABLE_EVENT_TYPES: tuple[str, ...] = tuple(
    t for t in EVENT_TYPES if t not in SYSTEM_RAISED_EVENT_TYPES
)

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
    "credit_stress": "Credit stress: a BIS, ECB or FRED credit series crossed its threshold "
    "on a new observation",
    "trade_exposure": "Trade exposure change: Comtrade flow for a mapped holding exposure "
    "moved beyond the threshold",
    "patent_status": "Material patent legal-status change for a holding's evidenced family",
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
    "credit_stress": 2,
    "trade_exposure": 2,
    "patent_status": 1,
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

    service: Literal[
        "rns", "eqs", "tdnet", "hkex", "cninfo", "dart", "gazette", "filings", "any"
    ] = "any"
    pattern: str = Field(
        min_length=1,
        max_length=200,
        description="Case-insensitive regular expression matched against the announcement "
        "type (RNS: the headline, e.g. 'Final Results'; EQS: the category, e.g. 'Ad-hoc'; "
        "TDnet, CNINFO, DART and HKEX: the title in the filing's own language).",
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
        # Tokyo (TDnet): titles are Japanese. Forecast revisions come before results.
        ("tdnet", r"業績予想の修正|配当予想の修正|業績予想.{0,6}修正|下方修正|上方修正",
         "guidance_change"),
        ("tdnet", r"決算短信|四半期報告|有価証券報告|決算概要|決算補足", "earnings_release"),
        ("tdnet", r"自己株式|配当|株主還元|株式分割|新株予約権|公募|第三者割当",
         "capital_allocation"),
        ("tdnet", r"合併|買収|株式取得|公開買付|子会社化|株式交換|事業譲渡|吸収分割",
         "m_and_a"),
        ("tdnet", r"代表取締役|役員の異動|役員人事|人事異動", "management_change"),
        # Hong Kong (HKEXnews): English titles plus the exchange's category text.
        ("hkex", r"profit warning|positive profit alert|profit alert", "guidance_change"),
        ("hkex", r"(annual|interim|final|quarterly|third quarter|first quarter) results|"
         r"results announcement", "earnings_release"),
        ("hkex", r"connected transaction|discloseable transaction|acquisition|disposal|"
         r"takeover|general offer|merger|privatisation", "m_and_a"),
        ("hkex", r"repurchase|buy-?back|dividend|placing|rights issue|open offer|"
         r"subscription of new shares|share scheme", "capital_allocation"),
        ("hkex", r"change of director|appointment|resignation|retirement|change in "
         r"(chairman|chief)", "management_change"),
        ("hkex", r"dealings? by|director.?s? interest|disclosure of interests",
         "insider_transaction"),
        # Shanghai and Shenzhen (CNINFO): Chinese titles.
        ("cninfo", r"业绩预告|业绩快报|盈利预警|业绩预增|业绩预减|业绩修正", "guidance_change"),
        ("cninfo", r"年度报告|半年度报告|季度报告|年报|中报|一季报|三季报|财务报告",
         "earnings_release"),
        ("cninfo", r"收购|重组|并购|要约|吸收合并|资产出售|资产购买", "m_and_a"),
        ("cninfo", r"回购|分红|利润分配|权益分派|增发|配股|可转债|定向增发",
         "capital_allocation"),
        ("cninfo", r"董事|监事|高级管理人员|辞职|聘任|总经理", "management_change"),
        ("cninfo", r"增持|减持|权益变动|持股", "insider_transaction"),
        ("cninfo", r"立案|处罚|诉讼|仲裁|问询函|监管函", "regulatory_legal"),
        # Korea (DART): report names are Korean. Insider reports come before the generic ones.
        ("dart", r"임원.{0,3}주요주주|특정증권등소유상황", "insider_transaction"),
        ("dart", r"영업\(잠정\)실적|매출액또는손익구조|분기보고서|반기보고서|사업보고서",
         "earnings_release"),
        ("dart", r"합병|인수|양수도|영업양수|타법인주식", "m_and_a"),
        ("dart", r"자기주식|배당|유상증자|무상증자|전환사채|신주인수권", "capital_allocation"),
        ("dart", r"대표이사|임원.{0,3}변경|최대주주변경", "management_change"),
        # The Gazette (UK): insolvency and corporate-status notices.
        ("gazette", r"winding.?up|liquidat|administrat|receiver|insolven|strike.?off|"
         r"dissol", "entity_status"),
        # EDINET and Companies House filings stored by the disclosure jobs.
        ("filings", r"臨時報告書", "classify"),
        ("filings", r"有価証券報告書|四半期報告書|半期報告書|決算", "earnings_release"),
        ("filings", r"大量保有報告書|変更報告書", "insider_transaction"),
        ("filings", r"accounts|annual report", "earnings_release"),
        ("filings", r"appointment of|termination of appointment|change of (director|secretary)",
         "management_change"),
        ("filings", r"allotment|statement of capital|capital|resolution|memorandum",
         "capital_allocation"),
        ("any", r".", "classify"),
    ]  # fmt: skip
    return [
        AnnouncementRule.model_validate({"service": s, "pattern": p, "event_type": e})
        for s, p, e in rules
    ]


class CreditStressRule(ConfigModel):
    """A threshold on one stored macro series. Fires once, when a genuinely new observation
    (a later period than any known before it) is beyond the threshold and the previous
    period was not; a revision of an old period never fires."""

    key: str = Field(pattern=r"^[a-z0-9_]{1,60}$")
    label: str = Field(min_length=1, max_length=120)
    series_id: str = Field(
        min_length=1,
        max_length=60,
        description="`macro_observation.series_id`: a FRED/ECB series key from the jobs config "
        "or a BIS series key (e.g. debt_service).",
    )
    source: Literal["any", "fred", "alfred", "ecb", "eurostat", "bis", "oecd"] = "any"
    geography: str | None = Field(default=None, max_length=20)
    direction: Literal["above", "below"] = "above"
    threshold: float
    enabled: bool = True


def _default_credit_rules() -> list[CreditStressRule]:
    # Starting points the owner tunes; they are not calibrated against history.
    return [
        CreditStressRule(
            key="ea_bls_tightening",
            label="Euro-area bank lending survey: enterprise credit standards tightening",
            series_id="ea_bls_enterprise_credit_standards",
            source="ecb",
            threshold=10.0,
        ),
        CreditStressRule(
            key="bis_debt_service_us",
            label="BIS private-sector debt service ratio, US",
            series_id="debt_service",
            source="bis",
            geography="US",
            threshold=15.0,
        ),
    ]


class FreeSourceAlertsConfig(ConfigModel):
    """Rules for the `free_source_alerts` job (credit stress, trade exposure, patent status,
    confirmed company-graph events). Every decision, raised or not, is written to
    `free_source_alert_trail`."""

    enabled: bool = True
    lookback_days: int = Field(
        default=3,
        ge=1,
        le=30,
        description="Observations first known within this many days are evaluated; older "
        "ones are never alerted (a replayed backfill raises nothing). After an outage the "
        "window reaches back to the last fully evaluated run (see max_catchup_days).",
    )
    max_catchup_days: int = Field(
        default=30,
        ge=1,
        le=90,
        description="How far back a run reaches to catch up after missed runs: the window "
        "starts at the last fully evaluated run, never earlier than this many days ago.",
    )
    enforce_verification_gates: bool = Field(
        default=True,
        description="Consult the Phase 8 gates before raising an alert: the domain "
        "reconciliation gate (credit stress, trade exposure, patents) or the signal "
        "publication gate (confirmed graph events and relationships). Without recorded audit "
        "data a gate denies, and the decision is recorded as blocked_by_gate. Turn off only "
        "for a deployment that does not run the Phase 8 verification.",
    )
    throttle_hours: int = Field(
        default=24,
        ge=0,
        le=720,
        description="After an alert for one rule and scope (series, holding), further alerts "
        "for the same scope within this many hours are recorded as throttled. 0: off.",
    )
    max_alerts_per_run: int = Field(default=10, ge=1, le=100)
    credit_stress: list[CreditStressRule] = Field(default_factory=_default_credit_rules)
    trade_min_exposure_weight: float = Field(
        default=0.05,
        gt=0,
        le=1,
        description="Mapped exposure weight (share of the issuer) below which a trade change "
        "is not alerted. Unmapped (unknown) weight is never counted.",
    )
    trade_min_change_pct: float = Field(
        default=20.0, gt=0, description="Year-on-year change in the trade value that alerts."
    )
    patent_min_ownership_confidence: float = Field(default=0.8, ge=0, le=1)
    graph_event_types: list[Literal["deal", "financing", "leadership", "relationship"]] = Field(
        default_factory=lambda: cast(
            list[Literal["deal", "financing", "leadership", "relationship"]],
            ["deal", "leadership", "relationship"],
        ),
        description="Confirmed company events of these types alert. Suggested or disputed "
        "ones are listed for review only.",
    )
    graph_edge_types: list[str] = Field(
        default_factory=lambda: ["acquirer", "acquired", "executive", "customer", "supplier"],
        description="Confirmed company-graph edges of these types alert.",
    )


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
            # The daily effective rate (DFF) drifts by a basis point or two; a Fed decision
            # moves it by 25 bp, so only moves beyond 0.1 pp count.
            "fed_funds": 0.1,
            # A ČNB decision (the repo file lists changes only). CZEONIA has no band: it
            # moves every day and is stored for the Sharpe ratio, not for events.
            "cnb_repo_2w": 0.0,
        },
        description="Per macro series key: a new release that moves more than this against "
        "the previous one is a macro surprise (in the series' own unit; 0: any change, e.g. "
        "a policy rate set exactly; fed_funds is the daily effective rate, which drifts, so "
        "it needs a band). Evaluated only on release days.",
    )
    free_source_alerts: FreeSourceAlertsConfig = Field(default_factory=FreeSourceAlertsConfig)
    announcement_types: list[AnnouncementRule] = Field(
        default_factory=_default_announcement_rules,
        description="How RNS and EQS announcement types map to event types.",
    )
    earnings_check_on: list[EventType] = Field(
        default_factory=lambda: cast(list[EventType], ["earnings_release"]),
        description="Confirmed stories of these types queue an earnings thesis check for "
        "the holding, unless one ran within earnings_check_cooldown_days.",
    )
    earnings_check_cooldown_days: int = Field(
        default=10,
        ge=1,
        le=60,
        description="A story does not queue an earnings check when one was queued or ran for "
        "the holding within this many days.",
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
