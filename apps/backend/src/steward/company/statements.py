"""The company statements page (plan M2.14): as-reported and standardized income statement,
balance sheet and cash-flow statement, annual, quarterly and trailing twelve months.

Every cell carries the filed fact it came from (concept, filing, period) or says how it was
derived; restated values keep their original; a period that cannot be built is absent and named
in `missing_periods`. Figures are computed here, in the backend, from stored facts.
"""

# ruff: noqa: E501

from __future__ import annotations

import datetime as dt
import itertools
import json
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Sequence
from decimal import Decimal
from typing import Any, Literal

import asyncpg
from pydantic import BaseModel, Field

from steward.analytics.contract import SourceRef, input_hash, rounded
from steward.analytics.versions import version
from steward.company.series import (
    MATCH_DAYS,
    Obs,
    Series,
    Val,
    View,
    build_series,
    cluster_ends,
    nearest_end,
)
from steward.company.standard import CHART, INSTANT_STATEMENTS, STATEMENT_LABEL
from steward.sources.xbrl_concepts import EQUITY_INCLUDING_NCI, STATEMENT_ITEMS, classify

Basis = Literal["standardized", "reported"]
Frequency = Literal["annual", "quarterly"]
Transform = Literal["none", "common_size", "yoy"]
STATEMENTS = ("income", "balance_sheet", "cash_flow")
MAX_COLUMNS = {"annual": 10, "quarterly": 12}
UNIT_DIVISOR = {1: Decimal(1), 1_000: Decimal(1000), 1_000_000: Decimal(1_000_000)}


class Cell(BaseModel):
    value: Decimal | None = None
    reason: str | None = Field(default=None, description="Why there is no value, or a note.")
    concept: str | None = None
    accession: str | None = None
    form: str | None = None
    filing_url: str | None = None
    source_url: str | None = None
    known_at: dt.datetime | None = None
    derived: str | None = Field(
        default=None, description="How the value was computed, if not filed."
    )
    restated: bool = False
    original_value: Decimal | None = None
    reconciled: bool | None = None
    unit: str = Field(
        default="money", description="money, per_share, shares or fraction (after a transform)."
    )


class DimensionDetail(BaseModel):
    column: str
    members: dict[str, str]
    value: Decimal
    accession: str | None = None


class Line(BaseModel):
    key: str
    label: str
    kind: str
    unit: str
    concepts: list[str] = Field(default_factory=list, description="Source concepts that map here.")
    cells: dict[str, Cell] = Field(default_factory=dict)
    dimensions: list[DimensionDetail] = Field(default_factory=list)


class Column(BaseModel):
    id: str
    kind: Literal["annual", "quarter", "ttm"]
    end: dt.date
    label: str
    notes: list[str] = Field(default_factory=list)


class StatementTable(BaseModel):
    statement: str
    label: str
    lines: list[Line]


class StatementCheck(BaseModel):
    name: str
    statement: str
    column: str
    passed: bool | None
    detail: str


class StatementsView(BaseModel):
    ticker: str
    basis: Basis
    frequency: Frequency
    version_view: View
    units: int
    transform: Transform
    per_share: bool
    currency: str | None = Field(
        description="The currency of the figures shown (the converted one when a base currency was chosen)."
    )
    native_currency: str | None = Field(
        default=None, description="The currency the facts were filed in."
    )
    columns: list[Column]
    statements: list[StatementTable]
    checks: list[StatementCheck]
    missing_periods: list[str]
    warnings: list[str]
    method_version: str
    as_of: dt.date | None
    known_at: dt.datetime | None
    sources: list[SourceRef]
    input_hash: str
    note: str


# ------------------------------------------------------------------------------- loading


def _dims(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


def _validation(value: Any) -> dict[str, Any]:
    return _dims(value)


def _members(dims: dict[str, Any]) -> dict[str, str]:
    """Axis -> member. The XBRL writers store a list of [axis, member] pairs; a dict is also read."""
    raw = dims.get("members") or dims.get("typed") or {}
    if isinstance(raw, dict):
        return {str(k): str(v) for k, v in raw.items()}
    if isinstance(raw, list):
        return {str(p[0]): str(p[1]) for p in raw if isinstance(p, (list, tuple)) and len(p) == 2}
    return {}


async def load_rows(
    pool: asyncpg.Pool, ticker: str, cutoff: dt.datetime | None = None
) -> list[dict[str, Any]]:
    rows = await pool.fetch(
        """select id, concept, label, statement, line_item, period_start, period_end, fiscal_period,
                  value, unit, currency, dimensions, known_at, accession, form, filing_url,
                  source_url, validation
           from public.financial_statement_fact
           where ticker=$1 and value is not null and statement in ('income','balance_sheet','cash_flow','segment')
             and ($2::timestamptz is null or known_at<=$2)
           order by id""",
        ticker,
        cutoff,
    )
    return without_convenience_translations([dict(r) for r in rows])


def without_convenience_translations(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rows less the second-currency copies a filing prints for convenience.

    TSMC's 20-F states each figure in TWD and again translated into USD. Taking whichever came
    first mixed the two: revenue growth over three years read -62.3% (USD 121bn against TWD
    2,264bn) and ROIC 2.3%. A fact is dropped only when the same filing states the same concept,
    period and dimensions in the currency most of that filing's money facts are in."""
    by_filing: dict[Any, Counter[str]] = defaultdict(Counter)
    for r in rows:
        if r.get("currency"):
            by_filing[r.get("accession")][str(r["currency"])] += 1

    def key(r: dict[str, Any]) -> tuple[Any, ...]:
        return (r.get("accession"), r["concept"], r["period_start"], r["period_end"],
                json.dumps(r.get("dimensions"), sort_keys=True, default=str))  # fmt: skip

    main = {a: c.most_common(1)[0][0] for a, c in by_filing.items() if len(c) > 1}
    if not main:
        return rows
    in_main = {
        key(r) for r in rows if r.get("currency") and main.get(r.get("accession")) == r["currency"]
    }
    return [
        r
        for r in rows
        if not (
            r.get("currency")
            and r.get("accession") in main
            and r["currency"] != main[r.get("accession")]
            and key(r) in in_main
        )
    ]


def to_obs(
    rows: Iterable[dict[str, Any]], *, key: str = "line_item", usable_only: bool = False
) -> tuple[list[Obs], list[tuple[Obs, dict[str, str]]]]:
    """Facts as observations: (non-dimensional, dimensional with their members).
    `usable_only` drops facts a reconciliation check rejected."""
    plain: list[Obs] = []
    dimensional: list[tuple[Obs, dict[str, str]]] = []
    for r in rows:
        if not isinstance(r["value"], Decimal):
            continue
        concept = str(r["concept"])
        item = r.get("line_item") or classify(concept)[1]
        validation = _validation(r.get("validation"))
        reconciled = validation.get("reconciled")
        reconciled = reconciled if isinstance(reconciled, bool) else None
        if usable_only and reconciled is False:
            continue
        obs = Obs(
            key=(str(item or "") if key == "line_item" else concept),
            concept=concept,
            label=r.get("label"),
            statement=str(r["statement"]),
            start=r["period_start"],
            end=r["period_end"],
            value=r["value"],
            unit=str(r["unit"] or ""),
            currency=r.get("currency"),
            known_at=r["known_at"],
            accession=r.get("accession"),
            form=r.get("form"),
            filing_url=r.get("filing_url"),
            source_url=r.get("source_url"),
            reconciled=reconciled,
            fact_id=int(r["id"]),
            fiscal_period=r.get("fiscal_period"),
        )
        members = _members(_dims(r.get("dimensions")))
        if members:
            dimensional.append((obs, members))
        elif obs.key:
            plain.append(obs)
    return plain, dimensional


# ------------------------------------------------------------------------------- columns


def _label(kind: str, end: dt.date) -> str:
    return (
        f"FY {end:%b %Y}"
        if kind == "annual"
        else f"{end:%b %Y}"
        if kind == "quarter"
        else f"TTM {end:%b %Y}"
    )


def _columns(
    series: dict[str, Series], frequency: Frequency
) -> tuple[list[Column], list[str], list[str]]:
    flow = [s for s in series.values() if s.annual or s.quarter]
    pool_ends = [e for s in flow for e in (s.annual if frequency == "annual" else s.quarter)]
    ends = cluster_ends(pool_ends)[-MAX_COLUMNS[frequency] :]
    kind: Literal["annual", "quarter"] = "annual" if frequency == "annual" else "quarter"
    columns = [
        Column(id=f"{kind[0]}:{e.isoformat()}", kind=kind, end=e, label=_label(kind, e))
        for e in ends
    ]
    missing: list[str] = []
    notes: list[str] = []
    limit = 400 if frequency == "annual" else 100 + MATCH_DAYS
    expected = "annual period" if frequency == "annual" else "quarter"
    for a, b in itertools.pairwise(ends):
        gap = (b - a).days
        if gap > limit:
            n = round(gap / (365 if frequency == "annual" else 91)) - 1
            missing.append(f"{max(n, 1)} {expected}(s) missing between {a} and {b}")
        elif frequency == "annual" and not 350 <= gap <= 380:
            notes.append(f"Fiscal year length changed between {a} and {b} ({gap} days).")
    ttm_ends = [s.ttm_end for s in flow if s.ttm_end is not None]
    # In the annual view a TTM equal to the last fiscal year would only repeat that column.
    newer = not ends or max(ttm_ends, default=ends[-1]) - ends[-1] > dt.timedelta(days=MATCH_DAYS)
    if ttm_ends and (frequency == "quarterly" or newer):
        end = max(ttm_ends)
        columns.append(
            Column(id=f"t:{end.isoformat()}", kind="ttm", end=end, label=_label("ttm", end))
        )
    return columns, missing, notes


def _pick(s: Series | None, column: Column, statement: str) -> Val | None:
    if s is None:
        return None
    if statement in INSTANT_STATEMENTS:
        end = nearest_end(list(s.instant), column.end)
        return s.instant[end] if end else None
    if column.kind == "ttm":
        return s.ttm if s.ttm_end == column.end else None
    table = s.annual if column.kind == "annual" else s.quarter
    end = nearest_end(list(table), column.end)
    return table[end] if end else None


def _cell(val: Val | None, unit: str, why: str = "no filed value for this period") -> Cell:
    if val is None:
        return Cell(reason=why, unit=unit)
    o = val.obs
    return Cell(
        value=val.value,
        concept=o.concept,
        accession=o.accession,
        form=o.form,
        filing_url=o.filing_url,
        source_url=o.source_url,
        known_at=o.known_at,
        derived=val.derived,
        restated=val.restated,
        original_value=val.original,
        reconciled=o.reconciled,
        unit=unit,
    )


# ------------------------------------------------------------------------- standardized


def _concepts_of(line_item: str) -> list[str]:
    return [c for c, (_s, item) in STATEMENT_ITEMS.items() if item == line_item]


def _equity_without_nci(
    columns: Sequence[Column],
    resolved: dict[str, dict[str, Decimal | None]],
    cells: dict[str, dict[str, Cell]],
) -> None:
    """Make `equity` the parent's equity. A filer that tags only total equity (parent plus
    non-controlling interest) would otherwise have the interest counted twice wherever it is added
    to equity (invested capital) or taken out of liabilities-and-equity. With no interest filed,
    the total is the whole equity and is left as filed."""
    for col in columns:
        values = resolved[col.id]
        cell = cells.get("equity", {}).get(col.id)
        nci = values.get("noncontrolling_interest")
        if cell is None or cell.value is None or nci is None:
            continue
        if cell.concept not in EQUITY_INCLUDING_NCI:
            continue
        values["equity"] = values["equity_resolved"] = cell.value - nci
        cells["equity"][col.id] = cell.model_copy(
            update={
                "value": cell.value - nci,
                "derived": "total equity less non-controlling interest",
            }
        )


def _standardized(
    obs: Sequence[Obs], columns: list[Column], view: View, series: dict[str, Series]
) -> tuple[list[StatementTable], list[StatementCheck]]:
    tables: list[StatementTable] = []
    checks: list[StatementCheck] = []
    for statement in STATEMENTS:
        lines: list[Line] = []
        resolved: dict[str, dict[str, Decimal | None]] = {c.id: {} for c in columns}
        cells: dict[str, dict[str, Cell]] = {row.key: {} for row in CHART[statement]}
        # Pass one reads what was filed (or its fallback); pass two derives the "other" lines,
        # which need the reported totals whatever order the chart lists them in.
        for derive_pass in (False, True):
            if derive_pass and statement == "balance_sheet":
                _equity_without_nci(columns, resolved, cells)
            for row in CHART[statement]:
                if (row.derive is not None) is not derive_pass:
                    continue
                for col in columns:
                    values = resolved[col.id]

                    def get(k: str, values: dict[str, Decimal | None] = values) -> Decimal | None:
                        return values.get(k)

                    if row.derive is not None:
                        number, how = row.derive(get)
                        cell = (
                            Cell(value=number, derived=how, unit=row.unit)
                            if number is not None
                            else Cell(reason="needs a reported subtotal this period", unit=row.unit)
                        )
                        values[row.key] = number
                    else:
                        val = _pick(series.get(row.key), col, statement)
                        if val is None and row.fallback is not None:
                            number, how = row.fallback(get)
                            cell = (
                                Cell(value=number, derived=how, unit=row.unit)
                                if number is not None
                                else Cell(reason="no filed value for this period", unit=row.unit)
                            )
                            values[row.key] = number
                        else:
                            cell = _cell(val, row.unit)
                            values[row.key] = val.value if val else None
                    values[f"{row.key}_resolved"] = values[row.key]
                    cells[row.key][col.id] = cell
        for row in CHART[statement]:
            line = Line(
                key=row.key,
                label=row.label,
                kind=row.kind,
                unit=row.unit,
                concepts=_concepts_of(row.key),
                cells=cells[row.key],
            )
            if any(c.value is not None for c in line.cells.values()):
                lines.append(line)
        tables.append(
            StatementTable(statement=statement, label=STATEMENT_LABEL[statement], lines=lines)
        )
        checks.extend(_checks(statement, columns, resolved))
    return tables, checks


def _tolerance(*values: Decimal) -> Decimal:
    biggest = max((abs(v) for v in values), default=Decimal(0))
    return max(Decimal("0.5"), biggest * Decimal("0.0005"))  # rounding in filed thousands


def _checks(
    statement: str, columns: list[Column], resolved: dict[str, dict[str, Decimal | None]]
) -> list[StatementCheck]:
    out: list[StatementCheck] = []
    for col in columns:
        v = resolved[col.id]
        if statement == "balance_sheet":
            assets, both = v.get("total_assets"), v.get("total_liabilities_and_equity")
            if assets is None or both is None:
                out.append(
                    StatementCheck(
                        name="balance_sheet_balances",
                        statement=statement,
                        column=col.id,
                        passed=None,
                        detail="total assets or total liabilities and equity not filed for this period",
                    )
                )
            else:
                ok = abs(assets - both) <= _tolerance(assets, both)
                out.append(
                    StatementCheck(
                        name="balance_sheet_balances",
                        statement=statement,
                        column=col.id,
                        passed=ok,
                        detail=f"assets {assets} vs liabilities and equity {both}",
                    )
                )
        if statement == "balance_sheet":
            total = v.get("total_assets")
            parts = [
                v.get(k)
                for k in (
                    "current_assets",
                    "property_plant_equipment",
                    "goodwill",
                    "intangibles",
                    "right_of_use_asset",
                    "other_noncurrent_assets",
                )
            ]
            if total is not None and v.get("other_noncurrent_assets") is not None:
                built = sum((p for p in parts if p is not None), Decimal(0))
                out.append(
                    StatementCheck(
                        name="total_assets_equals_lines_plus_other",
                        statement=statement,
                        column=col.id,
                        passed=abs(built - total) <= _tolerance(built, total),
                        detail=f"lines {built} (incl. other) vs reported {total}",
                    )
                )
    return out


# --------------------------------------------------------------------------- as reported


def _as_reported(
    obs: Sequence[Obs],
    dimensional: Sequence[tuple[Obs, dict[str, str]]],
    columns: list[Column],
    view: View,
) -> list[StatementTable]:
    by_concept: dict[str, list[Obs]] = defaultdict(list)
    for o in obs:
        by_concept[o.concept].append(o)
    latest_accession: dict[str, tuple[dt.datetime, int]] = {}
    for concept, items in by_concept.items():
        newest = max(items, key=lambda o: (o.known_at, o.fact_id))
        peers = [o for o in items if o.accession == newest.accession]
        latest_accession[concept] = (newest.known_at, min(o.fact_id for o in peers))
    dims: dict[str, list[tuple[Obs, dict[str, str]]]] = defaultdict(list)
    for o, members in dimensional:
        dims[o.concept].append((o, members))
    tables: list[StatementTable] = []
    for statement in STATEMENTS:
        concepts = [c for c, items in by_concept.items() if items[0].statement == statement]
        # Filing order: the newest filing first, in the order that filing listed the line.
        concepts.sort(key=lambda c: (-latest_accession[c][0].timestamp(), latest_accession[c][1]))
        lines: list[Line] = []
        for concept in concepts:
            s = build_series(concept, by_concept[concept], view)
            sample = by_concept[concept][-1]
            unit = (
                "per_share"
                if "/" in sample.unit
                else "shares"
                if sample.unit.lower() == "shares"
                else "money"
            )
            line = Line(
                key=concept,
                label=sample.label or concept.split(":")[-1],
                kind="line",
                unit=unit,
                concepts=[concept],
            )
            for col in columns:
                line.cells[col.id] = _cell(_pick(s, col, statement), unit)
            if any(c.value is not None for c in line.cells.values()):
                for o, members in dims.get(concept, []):
                    for col in columns:
                        if col.kind == "ttm":
                            continue
                        if o.start is None and abs((o.end - col.end).days) <= MATCH_DAYS:
                            hit = True
                        else:
                            hit = (
                                o.start is not None
                                and abs((o.end - col.end).days) <= MATCH_DAYS
                                and (
                                    (col.kind == "annual" and 330 <= (o.end - o.start).days <= 380)
                                    or (
                                        col.kind == "quarter"
                                        and 80 <= (o.end - o.start).days <= 100
                                    )
                                )
                            )
                        if hit and len(line.dimensions) < 40:
                            line.dimensions.append(
                                DimensionDetail(
                                    column=col.id,
                                    members=members,
                                    value=o.value,
                                    accession=o.accession,
                                )
                            )
                lines.append(line)
        tables.append(
            StatementTable(statement=statement, label=STATEMENT_LABEL[statement], lines=lines)
        )
    return tables


# --------------------------------------------------------------------------- transforms


def _scaled(cell: Cell, divisor: Decimal) -> Cell:
    if cell.value is None or cell.unit != "money":
        return cell
    out = cell.model_copy()
    out.value = cell.value / divisor
    if cell.original_value is not None:
        out.original_value = cell.original_value / divisor
    return out


def _prior_column(columns: Sequence[Column], col: Column) -> Column | None:
    if col.kind == "ttm":
        return None
    target = col.end - dt.timedelta(days=365)
    same = [c for c in columns if c.kind == col.kind and abs((c.end - target).days) <= 21]
    return same[0] if same else None


def apply_transform(
    tables: list[StatementTable],
    columns: list[Column],
    *,
    transform: Transform,
    per_share: bool,
    units: int,
    standardized: bool,
) -> list[str]:
    """Scale, common-size, year-over-year or per-share, in place. Returns notes."""
    notes: list[str] = []
    divisor = UNIT_DIVISOR[units]
    income = next((t for t in tables if t.statement == "income"), None)
    balance = next((t for t in tables if t.statement == "balance_sheet"), None)
    base_keys = {"income": "revenue", "cash_flow": "revenue", "balance_sheet": "total_assets"}
    raw: dict[tuple[str, str, str], Decimal | None] = {}
    for t in tables:
        for line in t.lines:
            for cid, cell in line.cells.items():
                raw[(t.statement, line.key, cid)] = cell.value
    for t in tables:
        for line in t.lines:
            for col in columns:
                cell = line.cells[col.id]
                if cell.value is None:
                    continue
                if transform == "common_size":
                    if not standardized:
                        continue
                    source = balance if t.statement == "balance_sheet" else income
                    base = (
                        raw.get((source.statement, base_keys[t.statement], col.id))
                        if source
                        else None
                    )
                    if cell.unit != "money":
                        continue
                    if base is None or base <= 0:
                        line.cells[col.id] = Cell(
                            reason="n/m: no positive revenue or total assets to scale by",
                            unit="fraction",
                        )
                    else:
                        line.cells[col.id] = cell.model_copy(
                            update={
                                "value": rounded(cell.value / base, "fraction"),
                                "unit": "fraction",
                                "original_value": None,
                            }
                        )
                elif transform == "yoy":
                    prior = _prior_column(columns, col)
                    before = raw.get((t.statement, line.key, prior.id)) if prior else None
                    if cell.unit == "shares" or cell.unit == "per_share" or cell.unit == "money":
                        if before is None or before <= 0 or cell.value is None:
                            why = "n/m: prior period missing or not positive"
                            line.cells[col.id] = Cell(reason=why, unit="fraction")
                        else:
                            line.cells[col.id] = cell.model_copy(
                                update={
                                    "value": rounded((cell.value - before) / before, "fraction"),
                                    "unit": "fraction",
                                    "original_value": None,
                                }
                            )
                elif (
                    per_share
                    and standardized
                    and t.statement in ("income", "cash_flow")
                    and cell.unit == "money"
                ):
                    shares = raw.get(("income", "weighted_shares_diluted", col.id)) or raw.get(
                        ("income", "weighted_shares_basic", col.id)
                    )
                    if not shares or shares <= 0:
                        line.cells[col.id] = Cell(
                            reason="no share count for this period", unit="per_share"
                        )
                    else:
                        line.cells[col.id] = cell.model_copy(
                            update={
                                "value": rounded(cell.value / shares, "per_share"),
                                "unit": "per_share",
                                "original_value": None,
                            }
                        )
                else:
                    line.cells[col.id] = _scaled(cell, divisor)
    if transform == "common_size" and not standardized:
        notes.append(
            "Common-size needs the standardized view (it scales by revenue or total assets); showing as reported."
        )
    if per_share and not standardized:
        notes.append("Per-share needs the standardized view; showing as reported.")
    return notes


# ------------------------------------------------------------------------------- the view


def build_view(
    ticker: str,
    rows: Sequence[dict[str, Any]],
    *,
    basis: Basis = "standardized",
    frequency: Frequency = "annual",
    view: View = "restated",
    units: int = 1_000_000,
    transform: Transform = "none",
    per_share: bool = False,
    convert: tuple[str, Callable[[list[StatementTable], list[Column], str], list[str]]]
    | None = None,
) -> StatementsView:
    """`convert` is (target currency, a function that translates the money cells in place and
    returns notes); it runs before units and transforms so growth and common-size use converted
    figures. Only a single-currency company is converted."""
    standardized = basis == "standardized"
    plain, dimensional = to_obs(rows, key="line_item" if standardized else "concept")
    by_key: dict[str, list[Obs]] = defaultdict(list)
    for o in plain:
        by_key[o.key].append(o)
    series = {k: build_series(k, v, view) for k, v in by_key.items()}
    columns, missing, notes = _columns(series, frequency)
    warnings = list(notes)
    if standardized:
        tables, checks = _standardized(plain, columns, view, series)
    else:
        tables, checks = _as_reported(plain, dimensional, columns, view), []
    if not columns:
        warnings.append("No filed annual or quarterly statement facts are stored for this company.")
    native_set = {o.currency for o in plain if o.currency and o.unit.upper() == o.currency.upper()}
    native_currency = next(iter(sorted(native_set)), None)
    converted_to: str | None = None

    def _transform() -> None:
        if transform != "none" or per_share or units != 1:
            warnings.extend(
                apply_transform(
                    tables,
                    columns,
                    transform=transform,
                    per_share=per_share,
                    units=units,
                    standardized=standardized,
                )
            )

    # Growth is computed in the filing currency, so it never includes the FX move: year-over-year
    # runs before translation (translation skips the resulting fraction cells).
    yoy_first = transform == "yoy"
    if yoy_first:
        _transform()
    if convert is not None:
        target, translate = convert
        if len(native_set) == 1 and native_currency and native_currency != target:
            warnings.extend(translate(tables, columns, native_currency))
            converted_to = target
        elif native_currency == target:
            warnings.append(f"The statements are already in {target}; no conversion was needed.")
        else:
            warnings.append(
                "Facts are in more than one currency or none is stated, so no conversion was applied."
            )
    if not yoy_first:
        _transform()
    currencies = {o.currency for o in plain if o.currency and o.unit.upper() == o.currency.upper()}
    if len(currencies) > 1:
        warnings.append(
            f"Facts are in more than one currency ({', '.join(sorted(currencies))}); no conversion is applied."
        )
    restated = sum(1 for t in tables for ln in t.lines for c in ln.cells.values() if c.restated)
    if restated:
        warnings.append(
            f"{restated} figure(s) were restated by a later filing; the {view} value is shown, and the other is in the cell."
        )
    if any(
        c.derived and "YTD" in c.derived
        for t in tables
        for ln in t.lines
        for c in ln.cells.values()
    ):
        warnings.append(
            "Some quarters are the difference of year-to-date figures (marked on the cell), not filed three-month values."
        )
    failed = [c for c in checks if c.passed is False]
    if failed:
        warnings.append(
            f"{len(failed)} integrity check(s) failed; open the checks list before relying on those periods."
        )
    known = [o.known_at for o in plain]
    accessions = sorted({o.accession for o in plain if o.accession})[-20:]
    return StatementsView(
        ticker=ticker,
        basis=basis,
        frequency=frequency,
        version_view=view,
        units=units,
        transform=transform,
        per_share=per_share,
        currency=converted_to or next(iter(sorted(currencies)), None),
        native_currency=native_currency,
        columns=columns,
        statements=tables,
        checks=checks,
        missing_periods=missing,
        warnings=warnings,
        method_version=version("statements"),
        as_of=max((c.end for c in columns), default=None),
        known_at=max(known, default=None),
        sources=[SourceRef(kind="filing", ref=a) for a in accessions],
        input_hash=input_hash(
            {
                "ticker": ticker,
                "facts": sorted(o.fact_id for o in plain),
                "basis": basis,
                "view": view,
                "converted_to": converted_to,
            }
        ),
        note=(
            "Standardized lines map filed concepts to one chart of accounts; anything the chart does not "
            "itemize is shown as an 'other' line, so totals still hold. Only the line items the statement "
            "store keeps are shown, which may be fewer than the filing has."
            if standardized
            else "As reported: the issuer's own concepts and labels, newest filing's order. Only concepts the statement store keeps are shown."
        ),
    )


def csv_text(v: StatementsView) -> str:
    """The shown table as CSV with a provenance header (what, as-of, method, units)."""
    import csv
    import io

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(
        ["# StewardAI company statements", v.ticker, v.basis, v.frequency, f"units={v.units}"]
    )
    w.writerow(
        [
            "# method_version",
            v.method_version,
            "as_of",
            v.as_of,
            "known_at",
            v.known_at,
            "input_hash",
            v.input_hash,
        ]
    )
    for t in v.statements:
        w.writerow([])
        w.writerow([t.label])
        w.writerow(["line", "concept", *[c.label for c in v.columns]])
        for line in t.lines:
            w.writerow(
                [
                    line.label,
                    "; ".join(line.concepts[:2]),
                    *[
                        ("" if line.cells[c.id].value is None else str(line.cells[c.id].value))
                        + (" (derived)" if line.cells[c.id].derived else "")
                        + (" (restated)" if line.cells[c.id].restated else "")
                        for c in v.columns
                    ],
                ]
            )
        w.writerow(["# accession per column is in the API response"])
    return buf.getvalue()
