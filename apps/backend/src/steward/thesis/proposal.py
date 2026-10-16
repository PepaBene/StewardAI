"""Thesis proposals from a research report (Phase 3): a field-by-field diff the owner accepts.

A report can carry a proposal for the ticker's thesis. It is built in code from sentences the
report already wrote, cited and had checked; no new model text is added. Each proposed line
keeps the source ids behind it, and each field shows what is there now next to what is
proposed. Nothing changes the active thesis until the owner accepts fields: accepting saves a
new `theses` config version (the old one stays in the history) and only if the active version
is still the one the proposal was made against.

Fields a report can propose: why_owned, exit_conditions, pre_mortem. KPI thresholds,
scenario prices, probabilities, conviction and base rate stay with the owner: no cited
sentence supports a number there.
"""

from typing import Any, Literal

import asyncpg
from pydantic import BaseModel, Field

from steward.config.research import Authoring
from steward.config.thesis import ThesesConfig, ThesisConfig
from steward.earnings.verdicts import NEWEST_PERIOD_FIRST
from steward.research.sources import CitedSentence

ProposalField = Literal["why_owned", "exit_conditions", "pre_mortem"]
FIELDS: tuple[ProposalField, ...] = ("why_owned", "exit_conditions", "pre_mortem")
# field -> (report sections it draws from in order of preference, most lines)
SOURCES: dict[str, tuple[tuple[str, ...], int]] = {
    # One line: the thesis keeps a single why-owned sentence, so one is all accept can apply.
    "why_owned": (("executive_summary", "business"), 1),
    "exit_conditions": (("thesis_kpis",), 4),
    "pre_mortem": (("counter_case", "moat_risks"), 3),
}
LEFT_TO_OWNER = [
    "KPI thresholds and units",
    "scenario prices and probabilities",
    "conviction, main reason and the base-rate check",
]


class ProposedLine(BaseModel):
    text: str
    evidence_ids: list[str] = Field(description="Registry ids of the sources behind the line.")


class FieldChange(BaseModel):
    field: ProposalField
    kind: Literal["add", "change"]
    before: list[str] = Field(description="What the active thesis says now (empty if nothing).")
    after: list[ProposedLine]


class ThesisProposal(BaseModel):
    mode: Authoring
    ticker: str
    base_version_id: int | None = Field(
        description="The active `theses` version the proposal was made against."
    )
    has_thesis: bool
    changes: list[FieldChange]
    unchanged: list[ProposalField]
    left_to_owner: list[str]
    notes: list[str] = Field(default_factory=list)


class ThesisKpiStatus(BaseModel):
    kpi_id: str
    metric: str
    rule: str = Field(description="The configured condition, e.g. '>= 10 %'.")
    status: Literal["pass", "breach", "unknown"]
    observed_value: float | None
    period: str | None
    as_of: str | None
    rationale: str | None


class ThesisReview(BaseModel):
    ticker: str
    base_version_id: int | None
    why_owned: str | None
    kpis: list[ThesisKpiStatus]
    exit_conditions: list[str]
    notes: list[str] = Field(default_factory=list)


def _lines(
    sections: dict[str, list[CitedSentence]], names: tuple[str, ...], limit: int
) -> list[ProposedLine]:
    for name in names:
        kept = [s for s in sections.get(name, []) if s.check != "unsupported" and s.text.strip()]
        if kept:
            return [
                ProposedLine(text=s.text.strip(), evidence_ids=list(s.source_ids))
                for s in kept[:limit]
            ]
    return []


def _current(thesis: ThesisConfig | None, field: ProposalField) -> list[str]:
    if thesis is None:
        return []
    value = getattr(thesis, field)
    return [value] if isinstance(value, str) else list(value)


def build_proposal(
    mode: Authoring,
    ticker: str,
    thesis: ThesisConfig | None,
    base_version_id: int | None,
    sections: dict[str, list[CitedSentence]],
) -> ThesisProposal | None:
    """None for `research_only` and `review_thesis` (a review is a `ThesisReview`)."""
    if mode not in ("draft_thesis", "propose_update"):
        return None
    changes: list[FieldChange] = []
    unchanged: list[ProposalField] = []
    notes: list[str] = []
    for field in FIELDS:
        names, limit = SOURCES[field]
        after = _lines(sections, names, limit)
        if not after:
            notes.append(f"{field}: the report wrote nothing to propose.")
            continue
        before = _current(thesis, field)
        if before == [line.text for line in after]:
            unchanged.append(field)
            continue
        changes.append(
            FieldChange(field=field, kind="change" if before else "add", before=before, after=after)
        )
    if thesis is None and not any(c.field == "why_owned" for c in changes):
        notes.append("No thesis exists and the report gave no why-owned line, so none can start.")
        changes = []
    return ThesisProposal(
        mode=mode,
        ticker=ticker,
        base_version_id=base_version_id,
        has_thesis=thesis is not None,
        changes=changes,
        unchanged=unchanged,
        left_to_owner=LEFT_TO_OWNER,
        notes=notes,
    )


class ProposalError(ValueError):
    pass


def apply_changes(
    current: ThesisConfig | None, proposal: ThesisProposal, accept: list[ProposalField]
) -> ThesisConfig:
    """The thesis with the accepted fields replaced. Fields not accepted stay as they are."""
    if not accept:
        raise ProposalError("accept at least one field")
    by_field = {c.field: c for c in proposal.changes}
    unknown = [f for f in accept if f not in by_field]
    if unknown:
        raise ProposalError(f"the proposal has no change for: {sorted(unknown)}")
    data: dict[str, Any] = (
        current.model_dump(mode="json") if current else {"ticker": proposal.ticker}
    )
    for field in accept:
        texts = [line.text for line in by_field[field].after]
        data[field] = texts[0] if field == "why_owned" else texts
    if "why_owned" not in data:
        raise ProposalError("a new thesis needs the why-owned line accepted")
    return ThesisConfig.model_validate(data)


def stale_fields(
    current: ThesisConfig | None, proposal: ThesisProposal, accept: list[ProposalField]
) -> list[ProposalField]:
    """Accepted fields whose live value no longer equals what the proposal was made against."""
    by_field = {c.field: c for c in proposal.changes}
    return [f for f in accept if f in by_field and _current(current, f) != by_field[f].before]


def replace_thesis(config: ThesesConfig, thesis: ThesisConfig) -> ThesesConfig:
    """The theses config with this ticker's thesis replaced, or added."""
    others = [t for t in config.theses if t.ticker != thesis.ticker]
    return ThesesConfig(theses=[*others, thesis])


async def review_thesis(
    pool: asyncpg.Pool, ticker: str, thesis: ThesisConfig | None, base_version_id: int | None
) -> ThesisReview:
    """Each configured KPI against its latest stored verdict. Code only: no model text."""
    if thesis is None:
        return ThesisReview(
            ticker=ticker,
            base_version_id=base_version_id,
            why_owned=None,
            kpis=[],
            exit_conditions=[],
            notes=["There is no thesis for this ticker to review."],
        )
    rows = await pool.fetch(
        "select distinct on (kpi_id) kpi_id, status, observed_value, period, rationale,"  # noqa: S608
        " created_at from public.verdict where ticker = $1"
        f" order by kpi_id, {NEWEST_PERIOD_FIRST}",
        ticker,
    )
    latest = {r["kpi_id"]: r for r in rows}
    kpis: list[ThesisKpiStatus] = []
    for k in thesis.kpis:
        r = latest.get(k.id)
        kpis.append(
            ThesisKpiStatus(
                kpi_id=k.id,
                metric=k.metric,
                rule=f"{k.operator} {k.threshold:g} {k.unit}",
                status=r["status"] if r else "unknown",
                observed_value=float(r["observed_value"])
                if r and r["observed_value"] is not None
                else None,
                period=r["period"] if r else None,
                as_of=r["created_at"].date().isoformat() if r else None,
                rationale=r["rationale"] if r else "No verdict has been stored for this KPI.",
            )
        )
    return ThesisReview(
        ticker=ticker,
        base_version_id=base_version_id,
        why_owned=thesis.why_owned,
        kpis=kpis,
        exit_conditions=list(thesis.exit_conditions),
    )
