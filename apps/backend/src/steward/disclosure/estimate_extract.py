"""P7.2: deterministic, offline extraction of a published consensus file.

Input is a file already in hand: HTML bytes, XLSX rows already read into memory, or PDF text. This
module never fetches (retrieval goes through the governed fetcher) and never guesses:
  * it reads headings, footnotes, the panel size and description, the fiscal-year definition,
    currency and scale, reported versus adjusted basis, mean/median/high/low, and the metric and
    segment lines, each with the offset (or row) it came from;
  * a value it cannot read stays None ("not stated"), never zero; a line whose period, currency or
    basis cannot be established carries `issues` and is not stored as a comparable value;
  * fiscal quarters are placed on the calendar from the stated fiscal-year end, or from an explicit
    "Q3 FY2026 is the quarter ended ..." sentence, which wins (52/53-week calendars);
  * identical bytes give the identical version hash, so a refetch creates no new version.
"""

from __future__ import annotations

import calendar
import datetime as dt
import hashlib
import json
import re
from collections.abc import Container, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from steward.disclosure.parse import parse_html

EXTRACTOR_VERSION = "estimate-extract.v1"
MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
MONTHS.update({m.lower(): i for i, m in enumerate(calendar.month_abbr) if m})
CURRENCIES = r"USD|EUR|GBP|CHF|JPY|CAD|AUD|SEK|NOK|DKK|CZK|PLN"
SYMBOLS = {"$": "USD", "€": "EUR", "£": "GBP"}
SCALES = {
    "thousand": 3,
    "thousands": 3,
    "million": 6,
    "millions": 6,
    "mn": 6,
    "billion": 9,
    "billions": 9,
    "bn": 9,
}
NOT_STATED = {"", "-", "—", "–", "n/a", "na", "nm", "n.a.", "not stated", "not available"}  # noqa: RUF001
NON_ADDITIVE = re.compile(
    r"eliminat|inter-?segment|reconcil|unallocated|^total\b|^consolidated\b", re.I
)
FOOTNOTE_LINE = re.compile(r"^\s*(?:\(\d+\)|\[\d+\]|\*+|†|notes?\s*\d*\s*[:.])\s*(.+)$", re.I)


@dataclass(frozen=True)
class ExtractedValue:
    metric: str
    segment: str | None
    segment_taxonomy: str | None
    non_additive: bool
    restated_prior: bool
    fiscal_period: str | None
    period_start: dt.date | None
    period_end: dt.date | None
    fiscal_calendar: str | None
    currency: str | None
    unit: str
    scale: int
    basis: str | None
    basis_definition: str | None
    mean: Decimal | None
    median: Decimal | None
    high: Decimal | None
    low: Decimal | None
    analyst_count: int | None
    original_string: str
    table_index: int
    row_index: int
    start: int
    end: int
    issues: tuple[str, ...]

    @property
    def storable(self) -> bool:
        return not self.issues and self.period_end is not None and self.basis is not None


@dataclass(frozen=True)
class Extraction:
    kind: str
    content_hash: str
    headings: tuple[str, ...]
    footnotes: tuple[str, ...]
    panel_description: str | None
    panel_size: int | None
    panel_method: str | None
    fiscal_calendar: str | None
    published_on: dt.date | None
    values: tuple[ExtractedValue, ...]
    issues: tuple[str, ...]
    parser_version: str = EXTRACTOR_VERSION


@dataclass(frozen=True)
class _Grid:
    index: int
    heading: str
    header: tuple[str, ...]
    rows: tuple[tuple[tuple[str, ...], int, int], ...]  # cells, start, end


# ----------------------------------------------------------------------------- versions
def content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def rows_hash(rows: Sequence[Sequence[Any]]) -> str:
    """Version hash of an XLSX read into memory: the cell values, not the container bytes."""
    return content_hash(
        json.dumps([[None if c is None else str(c) for c in r] for r in rows]).encode()
    )


def is_new_version(known_hashes: Container[str], digest: str) -> bool:
    """Identical bytes are not a new version."""
    return digest not in known_hashes


# --------------------------------------------------------------------------- primitives
def parse_date(text: str) -> dt.date | None:
    t = text.strip().rstrip(".")
    if m := re.search(r"(\d{4})-(\d{2})-(\d{2})", t):
        try:
            return dt.date(int(m[1]), int(m[2]), int(m[3]))
        except ValueError:
            return None
    if m := re.search(r"(\d{1,2})\s+([A-Za-z]{3,9})\.?,?\s+(\d{4})", t):
        mon = MONTHS.get(m[2].lower())
        if mon:
            return _safe(int(m[3]), mon, int(m[1]))
    if m := re.search(r"([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})", t):
        mon = MONTHS.get(m[1].lower())
        if mon:
            return _safe(int(m[3]), mon, int(m[2]))
    return None


def _safe(y: int, m: int, d: int) -> dt.date | None:
    try:
        return dt.date(y, m, d)
    except ValueError:
        return None


def parse_number(raw: str) -> tuple[Decimal | None, str | None]:
    """(value, problem). A blank or "n/a" is (None, None): not stated, not zero."""
    s = raw.strip()
    if s.lower() in NOT_STATED:
        return None, None
    neg = s.startswith("(") and s.endswith(")")
    s = re.sub(r"[\s$€£%()]", "", s).replace("−", "-")  # noqa: RUF001
    # A comma is a thousands separator only between groups of three digits; "1,2,3" or "1,5" is a
    # different convention, and reading it as 123 or 15 would invent a number.
    if "," in s and not re.fullmatch(r"[-+]?\d{1,3}(,\d{3})+(\.\d+)?", s):
        return None, f"unreadable number {raw.strip()!r}"
    s = s.replace(",", "")
    try:
        v = Decimal(s)
    except InvalidOperation:
        return None, f"unreadable number {raw.strip()!r}"
    return (-v if neg else v), None


def _clean(label: str) -> str:
    return re.sub(r"\s*(\(\d+\)|\[\d+\]|\*+|†)\s*$", "", label.strip()).strip()


def _month_end(y: int, m: int) -> dt.date:
    return dt.date(y, m, calendar.monthrange(y, m)[1])


def _shift_months(d: dt.date, n: int) -> dt.date:
    idx = d.year * 12 + (d.month - 1) + n
    y, m = divmod(idx, 12)
    m += 1
    if d.day >= calendar.monthrange(d.year, d.month)[1]:
        return _month_end(y, m)
    return dt.date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def fiscal_year_end(text: str) -> tuple[int, int, str] | None:
    """(month, day, wording) from "fiscal year ends 30 September" and similar."""
    pat = r"fiscal\s+year\s+(?:ends?|ending|end\s+on)\s+(?:on\s+)?"
    if m := re.search(pat + r"(\d{1,2})\s+([A-Za-z]{3,9})", text, re.I):
        mon = MONTHS.get(m[2].lower())
        if mon:
            return mon, int(m[1]), f"fiscal year ends {int(m[1])} {calendar.month_name[mon]}"
    if m := re.search(pat + r"([A-Za-z]{3,9})\s+(\d{1,2})", text, re.I):
        mon = MONTHS.get(m[1].lower())
        if mon:
            return mon, int(m[2]), f"fiscal year ends {int(m[2])} {calendar.month_name[mon]}"
    return None


def explicit_periods(text: str) -> dict[str, dt.date]:
    """ "Q3 FY2026 is the quarter ended 28 June 2026" -> {"FY2026Q3": 2026-06-28}."""
    out: dict[str, dt.date] = {}
    for m in re.finditer(
        r"(Q[1-4]\s*FY\s*\d{2,4}|FY\s*\d{2,4}\s*Q[1-4])\s*(?:is|=|means|covers)\s+(?:the\s+)?"
        r"(?:quarter|three\s+months|year|twelve\s+months)\s+ended\s+([^.;\n]+)",
        text,
        re.I,
    ):
        label = normalise_period(m[1])
        end = parse_date(m[2])
        if label and end:
            out[label] = end
    return out


def normalise_period(label: str) -> str | None:
    s = label.strip()

    def year(x: str) -> int:
        n = int(x)
        return 2000 + n if n < 100 else n

    if m := re.search(r"Q([1-4])\s*(?:FY)?\s*'?(\d{2,4})\b", s, re.I):
        return f"FY{year(m[2])}Q{m[1]}"
    if m := re.search(r"(?:FY|fiscal(?:\s+year)?)\s*'?(\d{2,4})\s*[-\s]?Q([1-4])", s, re.I):
        return f"FY{year(m[1])}Q{m[2]}"
    if m := re.search(r"\b([1-4])Q\s*'?(\d{2,4})\b", s, re.I):
        return f"FY{year(m[2])}Q{m[1]}"
    if m := re.search(r"(?:FY|fiscal(?:\s+year)?)\s*'?(\d{2,4})\b", s, re.I):
        return f"FY{year(m[1])}"
    return None


def period_dates(
    label: str, fy_end: tuple[int, int, str] | None, explicit: dict[str, dt.date]
) -> tuple[dt.date | None, dt.date | None]:
    """(start, end) of a normalised label. Fiscal years are named by the calendar year in which
    they end. An explicit sentence overrides the computed end."""
    m = re.fullmatch(r"FY(\d{4})(?:Q([1-4]))?", label)
    if not m:
        return None, None
    y, q = int(m[1]), int(m[2]) if m[2] else 4
    end = explicit.get(label)
    if end is None:
        if fy_end is None:
            return None, None
        month, day, _ = fy_end
        anchor = _safe(y, month, day) or _month_end(y, month)
        end = _shift_months(anchor, -3 * (4 - q))
    if m[2] is None:
        return _shift_months(end, -12) + dt.timedelta(days=1), end
    prior = explicit.get(f"FY{y}Q{q - 1}") if q > 1 else None
    start = (
        (prior + dt.timedelta(days=1)) if prior else _shift_months(end, -3) + dt.timedelta(days=1)
    )
    return start, end


def panel_method_of(description: str | None) -> str | None:
    """The panel's methodology with its size removed: "14 analysts" and "15 analysts" are the same
    method; a different provider or selection rule is not."""
    if not description:
        return None
    s = re.sub(r"\d+", "", description.lower())
    return re.sub(r"[^a-z]+", " ", s).strip() or None


def _unit_for(metric: str, header_text: str) -> tuple[str, int]:
    low = metric.lower()
    if "per share" in low or re.search(r"\beps\b", low) or "dividend" in low:
        return "per_share", 0
    if "margin" in low or "growth" in low or "%" in low or "rate" in low:
        return "percent", 0
    m = re.search(r"\b(thousands?|millions?|billions?|mn|bn)\b", header_text, re.I)
    return "amount", SCALES[m[1].lower()] if m else 0


def _currency(text: str) -> str | None:
    if m := re.search(rf"\b({CURRENCIES})\b", text):
        return m[1]
    for sym, code in SYMBOLS.items():
        if sym in text:
            return code
    return None


def _basis(text: str) -> str | None:
    if re.search(r"adjusted|non-?gaap|non-?ifrs|underlying", text, re.I):
        return "adjusted"
    has_ifrs, has_gaap = bool(re.search(r"\bIFRS\b", text)), bool(re.search(r"\bGAAP\b", text))
    if has_ifrs and not has_gaap:
        return "IFRS"
    if has_gaap and not has_ifrs:
        return "GAAP"
    return None


def _reporting_standard(text: str) -> str | None:
    """The accounting standard the file names, ignoring the word 'adjusted'."""
    has_ifrs, has_gaap = bool(re.search(r"\bIFRS\b", text)), bool(re.search(r"\bGAAP\b", text))
    return "IFRS" if has_ifrs and not has_gaap else "GAAP" if has_gaap and not has_ifrs else None


_COLUMNS = (
    ("segment", r"segment|division|business\s+unit"),
    ("metric", r"metric|line\s+item|measure|item"),
    ("period", r"period|quarter|fiscal|year"),
    ("median", r"median"),
    ("mean", r"mean|average|consensus"),
    ("high", r"high|max"),
    ("low", r"low|min"),
    ("count", r"analyst|estimates?|# ?est|number|contributors"),
)


def _columns(header: Sequence[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for i, cell in enumerate(header):
        for name, pat in _COLUMNS:
            if name not in out and re.search(pat, cell, re.I):
                out[name] = i
                break
    return out


# --------------------------------------------------------------------------- the engine
def _extract(
    kind: str,
    digest: str,
    text: str,
    headings: list[str],
    grids: list[_Grid],
) -> Extraction:
    issues: list[str] = []
    footnotes = tuple(
        m[1].strip() for line in text.splitlines() if (m := FOOTNOTE_LINE.match(line))
    )
    panel_line = next(
        (
            s.strip()
            for s in re.split(r"(?<=[.!?])\s+|\n", text)
            if re.search(r"analyst|panel|contribut", s, re.I)
            and re.search(r"consensus|panel", s, re.I)
        ),
        None,
    )
    size = None
    if m := re.search(
        r"(\d+)\s+(?:sell-?side\s+|equity\s+research\s+)?(?:analysts|contributors|brokers)",
        text,
        re.I,
    ):
        size = int(m[1])
    if panel_line is None:
        issues.append("no panel description found")
    if size is None:
        issues.append("panel size not stated")
    fy = fiscal_year_end(text)
    explicit = explicit_periods(text)
    if fy is None and not explicit:
        issues.append("fiscal-year definition not stated")
    published = None
    if m := re.search(r"published\s*(?:on|:)?\s*([^.;\n]{6,30})", text, re.I):
        published = parse_date(m[1])
    doc_standard = _reporting_standard(text)
    seg_tax = None
    if m := re.search(
        r"segment\s+(?:definitions?|structure|reporting)\s*[:\-]?\s*([^.\n]{3,80})", text, re.I
    ):
        seg_tax = re.sub(r"\s+", " ", m[1].strip().lower())
    restated = bool(
        re.search(
            r"\b(?:have|has|were|are|was)\s+been\s+(?:recast|restated)\b|\bwere\s+(?:recast|restated)\b"
            r"|\brecast\s+to\s+reflect\b",
            text,
            re.I,
        )
    )
    doc_currency = _currency(text)
    basis_note = next(
        (f for f in footnotes if re.search(r"adjusted|excludes|non-?gaap", f, re.I)), None
    )

    values: list[ExtractedValue] = []
    for g in grids:
        cols = _columns(g.header)
        head_text = " ".join((*g.header, g.heading))
        cur = _currency(" ".join(g.header)) or _currency(g.heading) or doc_currency
        table_basis = (
            _basis(head_text)
            if re.search(
                r"adjusted|non-?gaap|non-?ifrs|underlying|\bGAAP\b|\bIFRS\b", head_text, re.I
            )
            else None
        )
        table_period = normalise_period(g.heading)
        is_segment = "segment" in cols or bool(re.search(r"segment", g.heading, re.I))
        if not any(k in cols for k in ("mean", "median", "high", "low")):
            issues.append(f"table {g.index} has no mean, median, high or low column")
            continue
        for cells, start, end in g.rows:
            row_issues: list[str] = []

            def cell(name: str, cells: tuple[str, ...] = cells, cols: dict[str, int] = cols) -> str:
                i = cols.get(name)
                return cells[i] if i is not None and i < len(cells) else ""

            seg_raw = _clean(cell("segment")) if "segment" in cols else ""
            metric = _clean(cell("metric")) or ("" if is_segment else _clean(cells[0]))
            if not metric:
                issues.append(f"table {g.index} row {start}: no metric named, row skipped")
                continue
            label = _clean(cell("period")) or (table_period or "")
            fiscal = normalise_period(label) or table_period
            if fiscal is None:
                row_issues.append("fiscal period not recognised")
            start_d, end_d = period_dates(fiscal, fy, explicit) if fiscal else (None, None)
            if fiscal and end_d is None:
                row_issues.append("fiscal period cannot be placed on the calendar")
            nums: dict[str, Decimal | None] = {}
            for name in ("mean", "median", "high", "low"):
                v, prob = parse_number(cell(name))
                nums[name] = v
                if prob:
                    row_issues.append(prob)
            count: int | None = None
            if "count" in cols:
                c, prob = parse_number(cell("count"))
                count = int(c) if c is not None and c == c.to_integral_value() else None
                if prob:
                    row_issues.append(prob)
            if all(v is None for v in nums.values()):
                row_issues.append("no value is stated")
            unit, scale = _unit_for(metric, head_text)
            row_basis = _basis(metric) or table_basis or doc_standard
            if row_basis is None:
                row_issues.append("reported or adjusted basis not stated")
            if unit != "percent" and cur is None and unit in ("amount", "per_share"):
                row_issues.append("currency not stated")
            segment = seg_raw or None
            values.append(
                ExtractedValue(
                    metric=metric,
                    segment=segment,
                    segment_taxonomy=seg_tax if segment else None,
                    non_additive=bool(segment and NON_ADDITIVE.search(segment)),
                    restated_prior=restated and bool(segment),
                    fiscal_period=fiscal,
                    period_start=start_d,
                    period_end=end_d,
                    fiscal_calendar=fy[2] if fy else None,
                    currency=cur if unit != "percent" else None,
                    unit=unit,
                    scale=scale,
                    basis=row_basis,
                    basis_definition=basis_note if row_basis == "adjusted" else None,
                    mean=nums["mean"],
                    median=nums["median"],
                    high=nums["high"],
                    low=nums["low"],
                    analyst_count=count,
                    original_string=" | ".join(cells),
                    table_index=g.index,
                    row_index=len([v for v in values if v.table_index == g.index]),
                    start=start,
                    end=end,
                    issues=tuple(row_issues),
                )
            )
    if not values:
        issues.append("no estimate lines were extracted")
    return Extraction(
        kind=kind,
        content_hash=digest,
        headings=tuple(headings),
        footnotes=footnotes,
        panel_description=panel_line,
        panel_size=size,
        panel_method=panel_method_of(panel_line),
        fiscal_calendar=fy[2] if fy else None,
        published_on=published,
        values=tuple(values),
        issues=tuple(issues),
    )


# ---------------------------------------------------------------------------- entry points
def extract_html(data: bytes) -> Extraction:
    doc = parse_html(data)
    sections = [s for s in doc.sections if not s.toc]
    grids: list[_Grid] = []
    for t in doc.tables:
        if len(t.rows) < 2:
            continue
        head = next((s.title for s in reversed(sections) if s.start <= t.start), "")
        grids.append(
            _Grid(
                t.index,
                head,
                tuple(c.strip() for c in t.rows[0].cells),
                tuple((tuple(c.strip() for c in r.cells), r.start, r.end) for r in t.rows[1:]),
            )
        )
    return _extract("html", content_hash(data), doc.text, [s.title for s in sections], grids)


def _scan_rows(rows: Sequence[Sequence[str]], *, kind: str, digest: str) -> Extraction:
    """Shared by XLSX rows and PDF text lines. A run of rows with three or more cells is a table
    (its first row is the header); a lone cell above it is its heading; any other lone cell is
    context text such as a footnote or the panel sentence."""
    cleaned = [[("" if c is None else str(c)).strip() for c in r] for r in rows]
    text_lines: list[str] = []
    headings: list[str] = []
    grids: list[_Grid] = []
    run: list[tuple[list[str], int]] = []
    last_heading = ""

    def flush() -> None:
        nonlocal run
        if len(run) >= 2:
            grids.append(
                _Grid(
                    len(grids),
                    last_heading,
                    tuple(run[0][0]),
                    tuple((tuple(c), i, i) for c, i in run[1:]),
                )
            )
        run = []

    for i, cells in enumerate(cleaned):
        filled = [c for c in cells if c]
        if len(filled) >= 3 or (run and len(filled) >= 2 and len(cells) >= len(run[0][0])):
            run.append((cells, i))
            continue
        flush()
        if len(filled) == 1:
            line = filled[0]
            text_lines.append(line)
            nxt = cleaned[i + 1] if i + 1 < len(cleaned) else []
            if len([c for c in nxt if c]) >= 3 and len(line) < 80:
                last_heading = line
                headings.append(line)
    flush()
    return _extract(kind, digest, "\n".join(text_lines), headings, grids)


def extract_rows(rows: Sequence[Sequence[Any]]) -> Extraction:
    """An XLSX sheet already read into memory (a list of rows of cell values)."""
    return _scan_rows(
        [[("" if c is None else str(c)) for c in r] for r in rows],
        kind="xlsx",
        digest=rows_hash(rows),
    )


def extract_text(text: str) -> Extraction:
    """PDF text: columns are separated by a tab, a pipe or two or more spaces."""
    rows = [re.split(r"\t|\s{2,}|\|", line.strip()) for line in text.splitlines()]
    return _scan_rows(rows, kind="pdf", digest=content_hash(text.encode()))
