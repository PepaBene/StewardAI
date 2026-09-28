"""Reading stored SEC XBRL fundamentals: revenue by region / segment / product for a holding
(GET /holdings/{ticker}/segments) and its latest shares outstanding (the 13F denominator).

Revenue rows are "as reported": the members and labels are the company's own, so regions of
different companies are never mapped onto one taxonomy. A period can appear in several
filings (a 10-K repeats the two years before it); the latest filing's figure is used, since
companies recast segments. Members flagged as subtotals (the sum of other members) are
returned but not counted in shares.
"""

import datetime as dt
import json
from collections import defaultdict
from decimal import Decimal
from typing import Any, Literal

import asyncpg
from pydantic import BaseModel, Field

from steward.config.holding_profiles import HoldingProfile, HoldingProfilesConfig
from steward.config.store import load_active
from steward.fundamentals.shares import METHOD_LABELS, basis_for

Kind = Literal["geographic", "segment", "product"]
PeriodBasis = Literal["annual", "quarter"]
KINDS: tuple[Kind, ...] = ("geographic", "segment", "product")
HISTORY_PERIODS = 8

SEC_TIER = "SEC EDGAR XBRL (official filing)"
MANUAL_TIER = "entered manually"


class SegmentPeriod(BaseModel):
    start: dt.date
    end: dt.date
    months: int
    basis: PeriodBasis | None
    label: str = Field(description='"FY2025" for a fiscal year, else "3M to 30 Jun 2026".')


class SegmentRow(BaseModel):
    label: str = Field(description="As the filing labels the member.")
    member: str | None = Field(description="XBRL member (e.g. country:US); null when manual.")
    value: Decimal
    share: float | None = Field(
        description="Fraction of the breakdown's sum (subtotals excluded); null for a subtotal."
    )
    subtotal: bool = Field(
        default=False, description="The sum of other members shown (e.g. a region group)."
    )
    concept: str | None = None
    label_source: Literal["filing", "derived", "manual"] = "filing"


class SegmentHistoryPoint(BaseModel):
    period: SegmentPeriod
    rows: list[SegmentRow]


class SegmentSource(BaseModel):
    kind: Literal["sec_xbrl", "manual"]
    tier: str = Field(description="Provenance label for the UI.")
    form: str | None = None
    accession: str | None = None
    filed: dt.date | None = None
    instance_url: str | None = None
    label_url: str | None = None
    filing_url: str | None = None
    note: str | None = Field(default=None, description="Manual rows: where they come from.")


class HoldingSegments(BaseModel):
    ticker: str
    basis: PeriodBasis | None = Field(description="The period basis shown.")
    available_bases: list[PeriodBasis]
    period: SegmentPeriod | None
    currency: str | None
    geographic: list[SegmentRow]
    segment: list[SegmentRow]
    product: list[SegmentRow]
    total_revenue: Decimal | None = Field(
        description="The filing's undimensioned revenue for the period (a breakdown can sum "
        "to less: hedging gains, unallocated items)."
    )
    history: dict[str, list[SegmentHistoryPoint]] = Field(
        description="Per breakdown, the same-basis periods stored, oldest first (at most 8)."
    )
    source: SegmentSource | None
    as_of: dt.date | None = Field(description="Filing date (SEC) or period end (manual).")
    notes: list[str] = Field(default_factory=list)


def months_between(start: dt.date, end: dt.date) -> int:
    return max(1, round(((end - start).days + 1) / 30.44))


def period_basis(months: int) -> PeriodBasis | None:
    if 11 <= months <= 13:
        return "annual"
    if months == 3:
        return "quarter"
    return None


def period_of(start: dt.date, end: dt.date) -> SegmentPeriod:
    months = months_between(start, end)
    basis = period_basis(months)
    label = f"FY{end.year}" if basis == "annual" else f"{months}M to {end.day} {end:%b %Y}"
    return SegmentPeriod(start=start, end=end, months=months, basis=basis, label=label)


def edgar_folder_url(instance_url: str | None) -> str | None:
    if not instance_url:
        return None
    return instance_url.rsplit("/", 1)[0] + "/"


def _shares(rows: list[dict[str, Any]]) -> list[SegmentRow]:
    counted = sum((r["value"] for r in rows if not r["subtotal"]), Decimal(0))
    out = [
        SegmentRow(
            label=r["label"],
            member=r["member"],
            value=r["value"],
            share=(
                float(r["value"] / counted)
                if counted > 0 and not r["subtotal"] and r["value"] >= 0
                else None
            ),
            subtotal=r["subtotal"],
            concept=r.get("concept"),
            label_source=r.get("label_source", "filing"),
        )
        for r in rows
    ]
    out.sort(key=lambda r: (r.subtotal, -r.value))
    return out


async def _sec_rows(pool: asyncpg.Pool, ticker: str) -> list[dict[str, Any]]:
    """Every stored row, the latest filing's winning for a (kind, member, period, unit)."""
    rows = await pool.fetch(
        "select r.kind, r.axis, r.member, r.label, r.label_source, r.concept, r.period_start,"
        " r.period_end, r.value, r.unit, r.subtotal, f.form, f.accession, f.filed,"
        " f.instance_url, f.label_url from public.segment_revenue r"
        " join public.xbrl_filing f on f.id = r.filing_id where f.ticker = $1"
        " order by f.filed desc, f.accession desc",
        ticker,
    )
    seen: set[tuple[Any, ...]] = set()
    out: list[dict[str, Any]] = []
    for r in rows:
        key = (r["kind"], r["member"], r["period_start"], r["period_end"], r["unit"])
        if key in seen:
            continue
        seen.add(key)
        out.append(dict(r))
    return out


def _manual_rows(profile: HoldingProfile | None) -> list[dict[str, Any]]:
    if profile is None:
        return []
    return [
        {
            "kind": m.axis,
            "member": None,
            "label": m.label,
            "label_source": "manual",
            "concept": None,
            "period_start": m.period_start,
            "period_end": m.period_end,
            "value": Decimal(str(m.value)),
            "unit": m.currency,
            "subtotal": False,
            "source": m.source,
        }
        for m in profile.manual_segments
    ]


def build_segments(
    ticker: str,
    rows: list[dict[str, Any]],
    requested: PeriodBasis | None,
    manual: bool,
) -> HoldingSegments:
    """The endpoint's answer from stored rows (pure, for tests)."""
    notes: list[str] = []
    breakdown = [r for r in rows if r["kind"] in KINDS]
    by_basis: dict[PeriodBasis, list[dict[str, Any]]] = defaultdict(list)
    for r in breakdown:
        basis = period_basis(months_between(r["period_start"], r["period_end"]))
        if basis is not None:
            by_basis[basis].append(r)
    available: list[PeriodBasis] = [b for b in ("annual", "quarter") if by_basis.get(b)]
    empty = HoldingSegments(
        ticker=ticker,
        basis=None,
        available_bases=available,
        period=None,
        currency=None,
        geographic=[],
        segment=[],
        product=[],
        total_revenue=None,
        history={},
        source=None,
        as_of=None,
        notes=notes,
    )
    if not available:
        if breakdown:
            notes.append("Only year-to-date periods (6 or 9 months) are stored; none is shown.")
        return empty
    basis = requested if requested in available else available[0]
    if requested and requested not in available:
        notes.append(f"No {requested} figures stored; showing {basis} instead.")
    pool_rows = by_basis[basis]
    latest_end = max(r["period_end"] for r in pool_rows)
    current = [r for r in pool_rows if r["period_end"] == latest_end]
    start = min(r["period_start"] for r in current)
    current = [r for r in current if r["period_start"] == start]
    units: dict[str, int] = defaultdict(int)
    for r in current:
        units[r["unit"]] += 1
    unit = max(units, key=lambda u: units[u])
    current = [r for r in current if r["unit"] == unit]
    totals = [
        r
        for r in rows
        if r["kind"] == "total"
        and r["period_start"] == start
        and r["period_end"] == latest_end
        and r["unit"] == unit
    ]
    history: dict[str, list[SegmentHistoryPoint]] = {}
    for kind in KINDS:
        points: dict[tuple[dt.date, dt.date], list[dict[str, Any]]] = defaultdict(list)
        for r in pool_rows:
            if r["kind"] == kind and r["unit"] == unit:
                points[(r["period_start"], r["period_end"])].append(r)
        keys = sorted(points, key=lambda k: k[1])[-HISTORY_PERIODS:]
        if keys:
            history[kind] = [
                SegmentHistoryPoint(period=period_of(*k), rows=_shares(points[k])) for k in keys
            ]
    first = current[0] if current else None
    if manual:
        sources = sorted({r["source"] for r in current if r.get("source")})
        source = SegmentSource(kind="manual", tier=MANUAL_TIER, note="; ".join(sources) or None)
        as_of = latest_end
    else:
        filing = max(current, key=lambda r: (r["filed"], r["accession"]), default=first)
        source = SegmentSource(
            kind="sec_xbrl",
            tier=SEC_TIER,
            form=filing["form"] if filing else None,
            accession=filing["accession"] if filing else None,
            filed=filing["filed"] if filing else None,
            instance_url=filing["instance_url"] if filing else None,
            label_url=filing["label_url"] if filing else None,
            filing_url=edgar_folder_url(filing["instance_url"]) if filing else None,
        )
        as_of = filing["filed"] if filing else None
    return HoldingSegments(
        ticker=ticker,
        basis=basis,
        available_bases=available,
        period=period_of(start, latest_end),
        currency=unit,
        geographic=_shares([r for r in current if r["kind"] == "geographic"]),
        segment=_shares([r for r in current if r["kind"] == "segment"]),
        product=_shares([r for r in current if r["kind"] == "product"]),
        total_revenue=totals[0]["value"] if totals else None,
        history=history,
        source=source,
        as_of=as_of,
        notes=notes,
    )


async def holding_segments(
    pool: asyncpg.Pool, ticker: str, basis: PeriodBasis | None = None
) -> HoldingSegments:
    rows = await _sec_rows(pool, ticker)
    if any(r["kind"] in KINDS for r in rows):
        return build_segments(ticker, rows, basis, manual=False)
    stored = await load_active(pool, "holding_profiles", HoldingProfilesConfig)
    profile = stored.body.for_ticker(ticker) if stored else None
    manual = _manual_rows(profile)
    if manual:
        return build_segments(ticker, manual, basis, manual=True)
    result = build_segments(ticker, [], basis, manual=False)
    parsed = await pool.fetchval(
        "select count(*) from public.xbrl_filing where ticker = $1", ticker
    )
    result.notes.append(
        "The latest SEC filing tags no revenue by region, segment or product."
        if parsed
        else "No SEC XBRL filing parsed for this holding and no manual rows in its holding "
        "profile (Config → Holding profiles)."
    )
    return result


# ---------------------------------------------------------------------- shares outstanding


class ShareClassOut(BaseModel):
    member: str
    label: str
    shares: Decimal
    counted: bool


class SharesOutstandingOut(BaseModel):
    shares: Decimal = Field(description="Home-market shares outstanding (the denominator).")
    as_of: dt.date
    method: str
    method_label: str
    classes: list[ShareClassOut] = Field(
        default_factory=list, description="Per class when the filing tags classes."
    )
    denominator_label: str = Field(description='e.g. "all share classes (A + B + C)".')
    accession: str | None
    form: str | None
    filed: dt.date | None
    source_url: str | None
    source_tier: str
    ads_ratio: Decimal = Field(description="Home-market shares per US-listed share applied.")
    ads_ratio_source: Literal["profile", "default"]
    ads_ratio_reported: Decimal | None = Field(
        default=None, description="The ratio the latest 20-F states, when read."
    )
    ads_ratio_text: str | None = None
    foreign_issuer: bool = Field(description="Files a 20-F / 40-F (its US line is an ADR/NYRS).")
    notes: list[str] = Field(default_factory=list)


async def latest_shares(
    pool: asyncpg.Pool, ticker: str, profile: HoldingProfile | None = None
) -> SharesOutstandingOut | None:
    row = await pool.fetchrow(
        "select * from public.shares_outstanding where ticker = $1"
        " order by fetched_at desc, id desc limit 1",
        ticker,
    )
    if row is None:
        return None
    if profile is None:
        stored = await load_active(pool, "holding_profiles", HoldingProfilesConfig)
        profile = (stored.body.for_ticker(ticker) if stored else None) or HoldingProfile(
            ticker=ticker
        )
    basis = basis_for(profile, row["cik"])
    classes = [ShareClassOut(**c) for c in _json_list(row["classes"])]
    counted = [c for c in classes if c.counted]
    if classes:
        names = " + ".join(c.label for c in counted)
        denominator = (
            f"all share classes ({names})"
            if len(counted) == len(classes)
            else f"{names} only (of {len(classes)} classes)"
        )
    else:
        denominator = "shares outstanding (single figure)"
    raw = _json_dict(row["raw"])
    notes = [str(n) for n in raw.get("notes") or []]
    reported = row["ads_ratio_reported"]
    if reported is not None and reported != basis.ads_ratio:
        notes.append(
            f"The latest 20-F states {reported} shares per ADS but {basis.ads_ratio} is applied "
            "(holding profile → share basis)."
        )
    form = row["form"] or ""
    return SharesOutstandingOut(
        shares=row["shares"],
        as_of=row["as_of"],
        method=row["method"],
        method_label=METHOD_LABELS.get(row["method"], row["method"]),
        classes=classes,
        denominator_label=denominator,
        accession=row["accession"],
        form=row["form"],
        filed=row["filed"],
        source_url=row["source_url"],
        source_tier=MANUAL_TIER if row["method"] == "manual" else SEC_TIER,
        ads_ratio=basis.ads_ratio,
        ads_ratio_source=basis.ads_ratio_source,
        ads_ratio_reported=reported,
        ads_ratio_text=row["ads_ratio_text"],
        foreign_issuer=form.startswith(("20-F", "40-F")),
        notes=notes,
    )


def _json_list(value: Any) -> list[dict[str, Any]]:
    data = json.loads(value) if isinstance(value, str) else value
    return [d for d in data or [] if isinstance(d, dict)]


def _json_dict(value: Any) -> dict[str, Any]:
    data = json.loads(value) if isinstance(value, str) else value
    return data if isinstance(data, dict) else {}
