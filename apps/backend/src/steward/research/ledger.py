"""The claim ledger (Phase 4): every sentence of a finished report as a typed, checked claim.

Built in code from the sections the run wrote and the source registry; no model call. A claim
has a type (fact, calculation, interpretation, strategy assessment, forecast, unknown), the
evidence behind it (each source with its tier, role and date), a confidence label, a period
and a review state. The label comes from the evidence, not from what the model says about
itself: a claim backed by a stored record or a primary source and passed by the verifier is
"high"; one that rests on a single secondary web source, or that nobody checked, is not; an
interpretation or a forecast is never above "medium", because no source can show it true.

`verify_ledger` is the deterministic pass after the claim verifier: every cited id exists,
figures and dates in a sentence are in the evidence it cites, the code-owned valuation figures
carry their inputs and formula, planned sections are not empty, a strategy section names the
rules and thesis KPIs it judges, the report has a counter-case and open questions, and
nothing rests on stale data without saying so.
"""

import datetime as dt
import re
from collections.abc import Iterable, Sequence
from typing import Literal

from pydantic import BaseModel, Field

from steward.research.gather import Valuation
from steward.research.sources import CheckStatus, CitedSentence, Registry, Source
from steward.verify.claims import missing_dates, missing_numbers

ClaimType = Literal[
    "fact", "calculation", "interpretation", "strategy_assessment", "forecast", "unknown"
]
Confidence = Literal["high", "medium", "low"]
ReviewState = Literal[
    "unreviewed", "machine_checked", "flagged", "owner_confirmed", "owner_rejected"
]
Severity = Literal["error", "warning", "info"]
Verdict = Literal["supports", "challenges", "mixed", "unknown"]

# Sections whose sentences are of one kind whatever they say.
SECTION_TYPE: dict[str, ClaimType] = {
    "valuation": "calculation",
    "open_questions": "unknown",
    "scenarios": "forecast",
    "catalysts": "forecast",
    "strategy_fit": "strategy_assessment",
    "thesis_kpis": "strategy_assessment",
}
INTERPRETIVE_SECTIONS = {"executive_summary", "counter_case", "moat_risks"}
HEDGE = re.compile(
    r"\b(may|might|could|likely|unlikely|probably|suggests?|appears?|seems?|arguably|"
    r"in our view|we think|should)\b",
    re.I,
)
FUTURE = re.compile(r"\b(will|expected to|is expected|forecast|guidance for|outlook)\b", re.I)
UNKNOWN = re.compile(
    r"\b(no (evidence|data|information)|not (disclosed|available|reported)|unclear|unknown|"
    r"could not (find|verify)|cannot be (determined|verified))\b",
    re.I,
)
# Records the app stores itself: code cites them, nobody has to trust a web page for them.
RECORD_KINDS = {
    "fact", "price", "listing", "position", "strategy", "thesis", "verdict", "filing13f",
    "relationship", "report", "observation",
}  # fmt: skip
# How old a source's date may be before a claim on it carries a stale notice.
STALE_DAYS = {"price": 7, "news_item": 90, "search": 180, "observation": 90}
STALE_DEFAULT = 550
INJECTION_CAVEAT = "instruction-like text"


class EvidenceRef(BaseModel):
    source_id: str
    kind: str
    role: str | None = None
    tier: int | None = None
    as_of: str | None = None
    retrieved_at: str | None = None
    exists: bool = True


class Claim(BaseModel):
    id: str = Field(description="c1, c2, ... in report order.")
    section_id: str
    index: int = Field(description="Position of the sentence in its section.")
    text: str
    type: ClaimType
    written_by: Literal["code", "model"]
    evidence: list[EvidenceRef]
    check: CheckStatus | None = None
    confidence: Confidence
    why: str = Field(description="What the confidence label rests on.")
    period: str | None = Field(default=None, description="The newest date among its sources.")
    stale: bool = False
    review: ReviewState = "unreviewed"
    rule: str | None = Field(
        default=None, description="For a strategy assessment: the rule or KPI it names."
    )


class LedgerCheck(BaseModel):
    code: str
    severity: Severity
    message: str
    claims: list[str] = Field(default_factory=list)


class StrategyCheck(BaseModel):
    """One configured rule or thesis KPI, the condition it states, and where the report stands."""

    kind: Literal["thesis_kpi", "strategy_rule"]
    id: str
    version: int | None = Field(description="The config version the rule comes from.")
    rule: str = Field(description="The condition as configured, e.g. 'gross_margin >= 10 %'.")
    status: Verdict
    evidence: str | None = None
    named_by: list[str] = Field(
        default_factory=list, description="Claims in the report that name it."
    )
    formal: bool = Field(
        description="True when the status comes from a stored check, not from the report's text."
    )


class Balance(BaseModel):
    has_support: bool
    has_challenge: bool
    has_unknowns: bool
    has_scenarios: bool
    missing: list[str]
    not_requested: list[str]


class Coverage(BaseModel):
    as_of: str
    data_from: str | None = None
    data_to: str | None = None
    stale_sources: list[str] = Field(default_factory=list)
    undated_sources: list[str] = Field(default_factory=list)
    notices: list[str] = Field(default_factory=list)


class ClaimLedger(BaseModel):
    version: Literal[1] = 1
    claims: list[Claim]
    by_type: dict[str, int]
    by_confidence: dict[str, int]
    checks: list[LedgerCheck]
    strategy: list[StrategyCheck]
    balance: Balance
    coverage: Coverage
    confidence: Confidence = Field(description="The report as a whole, from its claims and checks.")
    confidence_why: str

    def restricted(self, keep: set[tuple[str, int]]) -> "ClaimLedger":
        """The ledger for a view that shows only some sentences (section id, position). The
        checks and the report's confidence stay those of the whole report."""
        claims = [c for c in self.claims if (c.section_id, c.index) in keep]
        return self.model_copy(
            update={
                "claims": claims,
                "by_type": _tally(c.type for c in claims),
                "by_confidence": _tally(c.confidence for c in claims),
            }
        )


def _tally(items: Iterable[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        counts[item] = counts.get(item, 0) + 1
    return counts


class SectionInput(BaseModel):
    """What the ledger needs of a report section."""

    id: str
    written_by: Literal["code", "model"]
    sentences: list[CitedSentence]
    note: str | None = None
    planned: bool = True


# ------------------------------------------------------------------ claim typing


def claim_type(section_id: str, written_by: str, text: str) -> ClaimType:
    fixed = SECTION_TYPE.get(section_id)
    if fixed is not None:
        return fixed
    if UNKNOWN.search(text):
        return "unknown"
    if written_by == "code":
        return "fact"
    if FUTURE.search(text) and not re.search(r"\b(was|were|rose|fell|reported)\b", text, re.I):
        return "forecast"
    if HEDGE.search(text) or section_id in INTERPRETIVE_SECTIONS:
        return "interpretation"
    return "fact"


def _parse(day: str | None) -> dt.date | None:
    if not day:
        return None
    try:
        return dt.date.fromisoformat(day[:10])
    except ValueError:
        return None


def _strong(s: Source) -> bool:
    return s.kind in RECORD_KINDS or s.tier == 1 or s.role == "primary"


def _flagged(s: Source) -> bool:
    return bool(s.caveat and INJECTION_CAVEAT in s.caveat)


def _stale(s: Source, today: dt.date) -> bool:
    day = _parse(s.as_of)
    if day is None:
        return False
    limit = STALE_DAYS.get(s.kind, STALE_DEFAULT)
    return (today - day).days > limit


def _rate(
    ctype: ClaimType,
    written_by: str,
    check: CheckStatus | None,
    sources: Sequence[Source],
    missing: int,
) -> tuple[Confidence, str]:
    if missing:
        return "low", "cites a source that is not in the report"
    if ctype == "unknown":
        return "low", "states what is not known"
    if not sources:
        return "low", "cites nothing"
    if any(_flagged(s) for s in sources):
        return "low", "rests on a source with instruction-like text"
    if check == "unsupported":
        return "low", "the claim verifier found it unsupported"
    if written_by == "model" and check != "supported":
        return "low", "no claim check ran on it"
    strong = [s for s in sources if _strong(s)]
    hosts = {s.url.split("/")[2] for s in sources if s.url and s.url.count("/") >= 2}
    if written_by == "code":
        return ("high", "built from a stored record") if strong else ("medium", "weak source")
    label: Confidence
    if strong:
        label, why = "high", "supported, on a stored record or a primary source"
    elif len(hosts) >= 2:
        label, why = "high", "supported by sources from different publishers"
    else:
        label, why = "medium", "supported, on one secondary source"
    if ctype in ("interpretation", "forecast", "strategy_assessment") and label == "high":
        return "medium", "a judgment: no source can show it true, only the facts under it"
    return label, why


# ------------------------------------------------------------------ the ledger


def build_claims(sections: Sequence[SectionInput], reg: Registry, today: dt.date) -> list[Claim]:
    claims: list[Claim] = []
    for section in sections:
        for index, raw in enumerate(section.sentences):
            text = raw.text
            source_ids = raw.source_ids
            sources = [s for i in source_ids if (s := reg.get(i)) is not None]
            missing = len(source_ids) - len(sources)
            check_status: CheckStatus | None = raw.check
            ctype = claim_type(section.id, section.written_by, text)
            label, why = _rate(ctype, section.written_by, check_status, sources, missing)
            days = [d for s in sources if (d := _parse(s.as_of))]
            evidence = [
                EvidenceRef(
                    source_id=i,
                    kind=(src.kind if (src := reg.get(i)) else "missing"),
                    role=src.role if src else None,
                    tier=src.tier if src else None,
                    as_of=src.as_of if src else None,
                    retrieved_at=src.retrieved_at if src else None,
                    exists=src is not None,
                )
                for i in source_ids
            ]
            if check_status == "unsupported":
                review: ReviewState = "flagged"
            elif section.written_by == "code" or check_status == "supported":
                review = "machine_checked"
            else:
                review = "unreviewed"
            claims.append(
                Claim(
                    id=f"c{len(claims) + 1}",
                    section_id=section.id,
                    index=index,
                    text=text,
                    type=ctype,
                    written_by=section.written_by,
                    evidence=evidence,
                    check=check_status,
                    confidence=label,
                    why=why,
                    period=max(days).isoformat() if days else None,
                    stale=any(_stale(s, today) for s in sources),
                    review=review,
                )
            )
    return claims


def _word_in(text: str, word: str) -> bool:
    return (
        bool(word)
        and re.search(rf"(?<![A-Za-z0-9_]){re.escape(word)}(?![A-Za-z0-9_])", text, re.I)
        is not None
    )


def strategy_checks(
    claims: list[Claim],
    kpis: Sequence[tuple[str, str, str, str | None]],
    rules: Sequence[tuple[str, str]],
    kpi_version: int | None,
    strategy_version: int | None,
) -> list[StrategyCheck]:
    """The thesis KPIs and strategy rules in force, each with the claims that name it.

    `kpis` are (id, rule text, status pass|breach|unknown, evidence) from the stored KPI
    verdicts; `rules` are (id, rule text). A KPI's status is the stored verdict (formal); a
    rule's is unknown here: the report's text cannot show compliance, the rule engine does.
    """
    out: list[StrategyCheck] = []
    judging = [c for c in claims if c.type == "strategy_assessment"]
    for kid, rule, status, evidence in kpis:
        named = [
            c.id for c in judging if _word_in(c.text, kid) or _word_in(c.text, rule.split()[0])
        ]
        verdict: Verdict = {"pass": "supports", "breach": "challenges"}.get(status, "unknown")  # type: ignore[assignment]
        out.append(
            StrategyCheck(
                kind="thesis_kpi", id=kid, version=kpi_version, rule=rule, status=verdict,
                evidence=evidence, named_by=named, formal=status != "unknown",
            )
        )  # fmt: skip
    for rid, rule in rules:
        named = [c.id for c in judging if _word_in(c.text, rid)]
        out.append(
            StrategyCheck(
                kind="strategy_rule", id=rid, version=strategy_version, rule=rule,
                status="unknown", evidence=None, named_by=named, formal=False,
            )
        )  # fmt: skip
    return out


def _balance(claims: list[Claim], sections: Sequence[SectionInput]) -> Balance:
    planned = {s.id for s in sections if s.planned}
    by_section: dict[str, int] = {}
    for c in claims:
        by_section[c.section_id] = by_section.get(c.section_id, 0) + 1
    has_support = any(
        c.confidence != "low" and c.section_id not in ("counter_case", "open_questions")
        for c in claims
    )
    has_challenge = by_section.get("counter_case", 0) + by_section.get("moat_risks", 0) > 0
    has_unknowns = by_section.get("open_questions", 0) > 0 or any(
        c.type == "unknown" for c in claims
    )
    has_scenarios = by_section.get("scenarios", 0) > 0
    missing: list[str] = []
    not_requested: list[str] = []
    for label, sections_for, present in (
        ("a counter-case", ("counter_case", "moat_risks"), has_challenge),
        ("open questions", ("open_questions",), has_unknowns),
        ("scenarios", ("scenarios",), has_scenarios),
    ):
        if not planned.intersection(sections_for):
            not_requested.append(label)
        elif not present:
            missing.append(label)
    return Balance(
        has_support=has_support,
        has_challenge=has_challenge,
        has_unknowns=has_unknowns,
        has_scenarios=has_scenarios,
        missing=missing,
        not_requested=not_requested,
    )


def _coverage(reg: Registry, claims: list[Claim], now: dt.datetime, notes: list[str]) -> Coverage:
    cited = {e.source_id for c in claims for e in c.evidence}
    used = [s for s in reg.sources if s.id in cited]
    days = [d for s in used if (d := _parse(s.as_of))]
    today = now.date()
    stale = [s.id for s in used if _stale(s, today)]
    undated = [s.id for s in used if not s.as_of and not s.retrieved_at]
    notices = list(notes)
    if stale:
        notices.append(f"{len(stale)} cited source(s) are older than their freshness limit.")
    if undated:
        notices.append(f"{len(undated)} cited source(s) carry no date.")
    return Coverage(
        as_of=now.isoformat(timespec="seconds"),
        data_from=min(days).isoformat() if days else None,
        data_to=max(days).isoformat() if days else None,
        stale_sources=stale,
        undated_sources=undated,
        notices=notices,
    )


def verify_ledger(
    claims: list[Claim],
    sections: Sequence[SectionInput],
    reg: Registry,
    valuation: Sequence[Valuation],
    strategy: Sequence[StrategyCheck],
    balance: Balance,
) -> list[LedgerCheck]:
    checks: list[LedgerCheck] = []

    def add(code: str, severity: Severity, message: str, ids: list[str] | None = None) -> None:
        checks.append(LedgerCheck(code=code, severity=severity, message=message, claims=ids or []))

    gone = [c.id for c in claims if any(not e.exists for e in c.evidence)]
    if gone:
        add("citation_target_missing", "error", "A claim cites a source that is not listed.", gone)
    bare = [
        c.id for c in claims if not c.evidence and c.type in ("fact", "calculation", "forecast")
    ]
    if bare:
        add(
            "uncited_material_claim",
            "error",
            "A fact, calculation or forecast cites nothing.",
            bare,
        )
    off_figure: list[str] = []
    off_date: list[str] = []
    undated: list[str] = []
    for c in claims:
        sources = [s for e in c.evidence if (s := reg.get(e.source_id))]
        texts = reg.evidence([s.id for s in sources])
        if missing_numbers(c.text, texts):
            off_figure.append(c.id)
        if missing_dates(c.text, texts):
            off_date.append(c.id)
        if (
            c.type in ("fact", "calculation")
            and c.period is None
            and any(ch.isdigit() for ch in c.text)
            and not any(s.kind in ("strategy", "thesis") for s in sources)
            and not any(s.retrieved_at for s in sources)
        ):
            undated.append(c.id)
    if off_figure:
        add(
            "figure_not_in_source",
            "warning",
            "A figure is not in the sources it cites.",
            off_figure,
        )
    if off_date:
        add("date_not_in_source", "warning", "A date in a claim is not in its sources.", off_date)
    if undated:
        add(
            "period_unknown",
            "warning",
            "A figure has no source date, so its period is open.",
            undated,
        )
    for v in valuation:
        known = [i for i in v.source_ids if reg.get(i) is not None]
        if v.value is not None and (not v.formula or not known):
            add(
                "calculation_inputs",
                "error",
                f"The {v.method} figure does not name its formula and input sources.",
            )
    stale = [c.id for c in claims if c.stale]
    if stale:
        add(
            "stale_source",
            "warning",
            "A claim rests on a source older than its freshness limit.",
            stale,
        )
    flagged = [
        c.id
        for c in claims
        if any(e.exists and (s := reg.get(e.source_id)) and _flagged(s) for e in c.evidence)
    ]
    if flagged:
        add("untrusted_instruction", "warning", "A source contains instruction-like text.", flagged)
    for section in sections:
        if section.planned and not section.sentences:
            severity: Severity = "warning" if section.note else "error"
            add("section_empty", severity, f"Section {section.id} has no cited sentence.")
    if any(s.id == "strategy_fit" and s.planned for s in sections):
        judged = [c for c in claims if c.section_id == "strategy_fit"]
        if judged and not any(st.named_by for st in strategy if st.kind == "strategy_rule"):
            add(
                "strategy_rule_unnamed", "warning", "The strategy section names no configured rule."
            )
    if any(s.id == "thesis_kpis" and s.planned for s in sections):
        unnamed = [st.id for st in strategy if st.kind == "thesis_kpi" and not st.named_by]
        if unnamed:
            add("kpi_unnamed", "warning", f"Thesis KPIs not named: {', '.join(unnamed)}.")
    breaches = [st.id for st in strategy if st.kind == "thesis_kpi" and st.status == "challenges"]
    if breaches:
        add("kpi_breach", "info", f"Thesis KPIs currently breached: {', '.join(breaches)}.")
    for label in balance.missing:
        add("balance_missing", "warning", f"The report has no {label}.")
    return checks


def report_confidence(claims: list[Claim], checks: list[LedgerCheck]) -> tuple[Confidence, str]:
    if not claims:
        return "low", "no claims"
    errors = [c for c in checks if c.severity == "error"]
    if errors:
        return (
            "low",
            f"{len(errors)} check(s) failed: {', '.join(sorted({c.code for c in errors}))}",
        )
    share = sum(1 for c in claims if c.confidence == "high") / len(claims)
    weak = sum(1 for c in claims if c.confidence == "low") / len(claims)
    warnings = {c.code for c in checks if c.severity == "warning"}
    if weak > 0.3:
        return "low", f"{weak:.0%} of claims are low confidence"
    if share >= 0.7 and not warnings:
        return "high", f"{share:.0%} of claims are high confidence and no check warns"
    return "medium", f"{share:.0%} of claims are high confidence" + (
        f"; warnings: {', '.join(sorted(warnings))}" if warnings else ""
    )


def build_ledger(
    sections: Sequence[SectionInput],
    reg: Registry,
    valuation: Sequence[Valuation],
    now: dt.datetime,
    *,
    kpis: Sequence[tuple[str, str, str, str | None]] = (),
    rules: Sequence[tuple[str, str]] = (),
    kpi_version: int | None = None,
    strategy_version: int | None = None,
    notes: list[str] | None = None,
) -> ClaimLedger:
    claims = build_claims(sections, reg, now.date())
    strategy = strategy_checks(claims, kpis, rules, kpi_version, strategy_version)
    balance = _balance(claims, sections)
    checks = verify_ledger(claims, sections, reg, valuation, strategy, balance)
    confidence, why = report_confidence(claims, checks)
    return ClaimLedger(
        claims=claims,
        by_type=_tally(c.type for c in claims),
        by_confidence=_tally(c.confidence for c in claims),
        checks=checks,
        strategy=strategy,
        balance=balance,
        coverage=_coverage(reg, claims, now, notes or []),
        confidence=confidence,
        confidence_why=why,
    )
