"""Segment, product and geography analysis with an owner mapping to other contexts (plan M2.12,
and the products and countries part of M2.1).

Built from the stored `segment_revenue` breakdowns (via `steward.fundamentals.read`). Rules:

* Segment revenue is never added up blindly. Members are classed by their filed label as
  operating, elimination (intersegment), unallocated (corporate, reconciling) or subtotal, the
  operating total is shown before and after eliminations, and the remainder against the filing's
  undimensioned total is shown as a difference, not hidden.
* A member that appears or disappears between periods is flagged as a changed definition rather
  than read as growth from or to zero.
* The owner can map a member to a country, commodity, patent technology or trade (HS) context.
  A mapping is an estimate the filing does not state; mapped exposure sums only mapped parts and
  keeps what is unmapped (and the unallocated revenue) as separate lines.
"""

# ruff: noqa: E501
from __future__ import annotations

import datetime as dt
import re
from collections import defaultdict
from collections.abc import Sequence
from decimal import Decimal
from typing import Any, Literal

import asyncpg
from pydantic import BaseModel, Field

from steward.analytics.contract import rounded
from steward.analytics.versions import version
from steward.fundamentals.read import HoldingSegments, PeriodBasis, SegmentRow, holding_segments

D = Decimal
Role = Literal["operating", "elimination", "unallocated", "subtotal"]
Context = Literal["country", "commodity", "patent_tech", "trade_hs"]
CONTEXT_LABEL: dict[str, str] = {
    "country": "Country",
    "commodity": "Commodity",
    "patent_tech": "Patent technology (CPC)",
    "trade_hs": "Trade product (HS code)",
}
ELIMINATION = re.compile(r"elimin|inter-?segment|inter-?company", re.I)
UNALLOCATED = re.compile(r"unallocat|corporate|reconcil|not allocated|adjustment", re.I)
REVENUE_DIFFERENCE_TOLERANCE = D("0.005")


class MemberLine(BaseModel):
    key: str
    label: str
    role: Role
    values: dict[str, Decimal | None] = Field(
        description="Revenue per period label; null = not reported that period."
    )
    latest_value: Decimal | None
    latest_share: Decimal | None = Field(
        description="Share of operating revenue before eliminations."
    )
    change: Decimal | None = Field(
        description="Latest against the previous period, only when both are reported."
    )
    definition_note: str | None = None


class Breakdown(BaseModel):
    kind: str
    label: str
    periods: list[str]
    latest_period: str | None
    members: list[MemberLine]
    operating_total: Decimal | None
    eliminations: Decimal | None
    unallocated: Decimal | None
    after_eliminations: Decimal | None
    reported_total: Decimal | None
    difference: Decimal | None
    difference_note: str | None
    notes: list[str]


class MapLine(BaseModel):
    id: int
    breakdown: str
    member_label: str
    context: str
    value: str
    share: Decimal
    note: str | None
    created_at: dt.datetime


class MapIn(BaseModel):
    breakdown: Literal["geographic", "segment", "product"]
    member_label: str = Field(min_length=1, max_length=300)
    context: Context
    value: str = Field(min_length=1, max_length=120)
    share: Decimal = Field(default=D(1), ge=0, le=1)
    note: str | None = Field(default=None, max_length=500)


class ExposureLine(BaseModel):
    value: str
    revenue: Decimal
    share: Decimal | None


class MappedExposure(BaseModel):
    breakdown: str
    context: str
    context_label: str
    period: str | None
    lines: list[ExposureLine]
    mapped: Decimal
    unmapped: Decimal
    unallocated: Decimal
    label: str = "Estimate from your mapping, not a reported figure"
    warnings: list[str]


class SegmentAnalysis(BaseModel):
    ticker: str
    basis: str | None
    available_bases: list[str]
    currency: str | None
    source_note: str | None
    breakdowns: list[Breakdown]
    mappings: list[MapLine]
    exposures: list[MappedExposure]
    notes: list[str]
    method_version: str


def role_of(row: SegmentRow) -> Role:
    if row.subtotal:
        return "subtotal"
    if ELIMINATION.search(row.label):
        return "elimination"
    if UNALLOCATED.search(row.label):
        return "unallocated"
    return "operating"


def _key(row: SegmentRow) -> str:
    return row.member or row.label


def _breakdown(kind: str, label: str, seg: HoldingSegments) -> Breakdown | None:
    points = seg.history.get(kind) or []
    if not points:
        return None
    periods = [p.period.label for p in points]
    latest_label = periods[-1]
    by_key: dict[str, dict[str, Decimal]] = defaultdict(dict)
    labels: dict[str, str] = {}
    roles: dict[str, Role] = {}
    for p in points:
        for r in p.rows:
            k = _key(r)
            by_key[k][p.period.label] = r.value
            labels[k] = r.label
            roles[k] = role_of(r)
    latest_rows = points[-1].rows
    previous_label = periods[-2] if len(periods) > 1 else None
    operating_latest = sum((r.value for r in latest_rows if role_of(r) == "operating"), D(0))
    lines: list[MemberLine] = []
    for k, vals in by_key.items():
        latest = vals.get(latest_label)
        prev = vals.get(previous_label) if previous_label else None
        note = None
        if previous_label and latest is not None and prev is None:
            note = (
                f"New in {latest_label}: not reported in {previous_label}, so no growth is shown."
            )
        elif previous_label and latest is None and prev is not None:
            note = f"Reported in {previous_label} but not in {latest_label}: the definition changed or the member was dropped."
        change = (
            (latest / prev - 1)
            if latest is not None
            and prev not in (None, D(0))
            and prev is not None
            and prev > 0
            and latest >= 0
            else None
        )
        lines.append(
            MemberLine(
                key=k,
                label=labels[k],
                role=roles[k],
                values={p: vals.get(p) for p in periods},
                latest_value=latest,
                latest_share=(
                    rounded(latest / operating_latest, "fraction")
                    if latest is not None and roles[k] == "operating" and operating_latest > 0
                    else None
                ),
                change=rounded(change, "fraction") if change is not None else None,
                definition_note=note,
            )
        )
    order = {"operating": 0, "unallocated": 1, "elimination": 2, "subtotal": 3}
    lines.sort(key=lambda m: (order[m.role], -(m.latest_value or D(0))))
    elim = sum((m.latest_value or D(0) for m in lines if m.role == "elimination"), D(0))
    unalloc = sum((m.latest_value or D(0) for m in lines if m.role == "unallocated"), D(0))
    has_elim = any(m.role == "elimination" and m.latest_value is not None for m in lines)
    has_unalloc = any(m.role == "unallocated" and m.latest_value is not None for m in lines)
    after = operating_latest + elim + unalloc
    total = seg.total_revenue if seg.period and seg.period.label == latest_label else None
    diff = total - after if total is not None else None
    diff_note = None
    if diff is not None and total and abs(diff) / abs(total) > REVENUE_DIFFERENCE_TOLERANCE:
        diff_note = (
            f"The members, eliminations and unallocated lines differ from the filing's total revenue by {rounded(diff, 'money')}; "
            "something is not broken out in this breakdown (for example hedging results or another axis)."
        )
    elif total is None:
        diff_note = "The filing's undimensioned total for this period is not stored, so the breakdown cannot be tied to it."
    notes: list[str] = []
    if any(m.role == "operating" for m in lines) and not has_elim and kind == "segment":
        notes.append(
            "No intersegment elimination line is tagged; the segment sum may include sales between segments."
        )
    return Breakdown(
        kind=kind,
        label={
            "geographic": "Geography",
            "segment": "Reported segments",
            "product": "Products and services",
        }[kind],
        periods=periods,
        latest_period=latest_label,
        members=lines,
        operating_total=operating_latest,
        eliminations=elim if has_elim else None,
        unallocated=unalloc if has_unalloc else None,
        after_eliminations=after,
        reported_total=total,
        difference=diff,
        difference_note=diff_note,
        notes=notes,
    )


def newest_mappings(rows: Sequence[dict[str, Any]]) -> list[MapLine]:
    """Newest row per (breakdown, member, context, value); a share of 0 removes the mapping."""
    seen: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for r in sorted(rows, key=lambda r: r["id"], reverse=True):
        k = (r["breakdown"], r["member_label"], r["context"], r["value"])
        seen.setdefault(k, r)
    return [
        MapLine(
            id=r["id"],
            breakdown=r["breakdown"],
            member_label=r["member_label"],
            context=r["context"],
            value=r["value"],
            share=r["share"],
            note=r["note"],
            created_at=r["created_at"],
        )
        for r in seen.values()
        if r["share"] > 0
    ]


def exposures(breakdowns: Sequence[Breakdown], maps: Sequence[MapLine]) -> list[MappedExposure]:
    out: list[MappedExposure] = []
    groups: dict[tuple[str, str], list[MapLine]] = defaultdict(list)
    for m in maps:
        groups[(m.breakdown, m.context)].append(m)
    for (kind, context), items in sorted(groups.items()):
        bd = next((b for b in breakdowns if b.kind == kind), None)
        if bd is None:
            continue
        warnings: list[str] = []
        by_member: dict[str, list[MapLine]] = defaultdict(list)
        for m in items:
            by_member[m.member_label.casefold()].append(m)
        revenue_by_value: dict[str, Decimal] = defaultdict(lambda: D(0))
        mapped_total = D(0)
        operating = [m for m in bd.members if m.role == "operating" and m.latest_value is not None]
        for member in operating:
            maps_here = by_member.get(member.label.casefold(), [])
            assigned = sum((x.share for x in maps_here), D(0))
            if assigned > 1:
                warnings.append(
                    f"{member.label}: your shares add to {assigned}, more than all of it; it is scaled down to 100%."
                )
            scale = D(1) / assigned if assigned > 1 else D(1)
            for x in maps_here:
                part = (member.latest_value or D(0)) * x.share * scale
                revenue_by_value[x.value] += part
                mapped_total += part
        for label in set(by_member) - {m.label.casefold() for m in operating}:
            warnings.append(
                f"A mapping for “{label}” matches no member in {bd.latest_period}; it was not counted."
            )
        op_total = bd.operating_total or D(0)
        lines = [
            ExposureLine(
                value=v,
                revenue=rounded(r, "money") or D(0),
                share=rounded(r / op_total, "fraction") if op_total > 0 else None,
            )
            for v, r in sorted(revenue_by_value.items(), key=lambda kv: -kv[1])
        ]
        out.append(
            MappedExposure(
                breakdown=kind,
                context=context,
                context_label=CONTEXT_LABEL[context],
                period=bd.latest_period,
                lines=lines,
                mapped=rounded(mapped_total, "money") or D(0),
                unmapped=rounded(max(op_total - mapped_total, D(0)), "money") or D(0),
                unallocated=rounded(bd.unallocated or D(0), "money") or D(0),
                warnings=warnings,
            )
        )
    return out


def build_analysis(ticker: str, seg: HoldingSegments, maps: Sequence[MapLine]) -> SegmentAnalysis:
    breakdowns = [
        b
        for kind, label in (
            ("segment", "Reported segments"),
            ("geographic", "Geography"),
            ("product", "Products"),
        )
        if (b := _breakdown(kind, label, seg)) is not None
    ]
    notes = list(seg.notes)
    if not breakdowns:
        notes.append("No revenue breakdown is stored for this company.")
    return SegmentAnalysis(
        ticker=ticker,
        basis=seg.basis,
        available_bases=list(seg.available_bases),
        currency=seg.currency,
        source_note=(
            seg.source.tier
            + (f", filing {seg.source.accession}" if seg.source and seg.source.accession else "")
        )
        if seg.source
        else None,
        breakdowns=breakdowns,
        mappings=list(maps),
        exposures=exposures(breakdowns, maps),
        notes=notes,
        method_version=version("segments"),
    )


async def load_mappings(pool: asyncpg.Pool, owner_id: Any, ticker: str) -> list[MapLine]:
    rows = await pool.fetch(
        "select id, breakdown, member_label, context, value, share, note, created_at"
        " from public.company_segment_map where owner_id=$1 and ticker=$2",
        owner_id,
        ticker,
    )
    return newest_mappings([dict(r) for r in rows])


async def save_mapping(pool: asyncpg.Pool, owner_id: Any, ticker: str, body: MapIn) -> None:
    await pool.execute(
        "insert into public.company_segment_map (owner_id, ticker, breakdown, member_label, context, value, share, note)"
        " values ($1,$2,$3,$4,$5,$6,$7,$8)",
        owner_id,
        ticker,
        body.breakdown,
        body.member_label.strip(),
        body.context,
        body.value.strip(),
        body.share,
        body.note,
    )


async def analysis(
    pool: asyncpg.Pool, owner_id: Any, ticker: str, basis: PeriodBasis | None
) -> SegmentAnalysis:
    seg = await holding_segments(pool, ticker, basis)
    maps = await load_mappings(pool, owner_id, ticker)
    return build_analysis(ticker, seg, maps)
