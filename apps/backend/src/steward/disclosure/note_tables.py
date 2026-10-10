"""P2.4: pull debt maturity, lease maturity and share repurchase tables out of note text.

Pure functions over the plain text of one note (a text block). Nothing is guessed:

- every extracted item keeps (start, end) character offsets into the input, so a reviewer can
  re-find the passage;
- a unit statement ("in millions") is required to scale a number; without one the number is kept
  raw, the scaled value is None and the row is sent to review;
- an em dash is a null, never zero; parentheses are negatives;
- an ambiguous row (unlabeled bucket, several value columns, unbalanced parenthesis, missing
  total, unknown share class) gets status "review" with its reasons, never a silent fix.
"""

# ruff: noqa: RUF001  (en and em dashes are the characters filings use, matched on purpose)
from __future__ import annotations

import datetime as dt
import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from itertools import pairwise
from typing import Literal

Status = Literal["extracted", "review"]

_MULT = {
    "thousands": Decimal(1000),
    "millions": Decimal(1_000_000),
    "billions": Decimal(1_000_000_000),
}


# ------------------------------------------------------------------------------------- units
@dataclass(frozen=True)
class UnitStatement:
    """What the note says its figures are in. `multiplier` is None when no statement was found or
    two statements disagree (`ambiguous`); nothing is then scaled."""

    multiplier: Decimal | None
    start: int | None = None
    end: int | None = None
    shares_multiplier: Decimal | None = None
    ambiguous: bool = False


_SHARES_UNIT = re.compile(r"(?i)\bshares?\b[^.\n]{0,40}?\bin\s+(thousands|millions|billions)\b")
_MONEY_UNIT = re.compile(
    r"(?i)\bin\s+(thousands|millions|billions)\b|\$\s*000s?\b|\(\s*000'?s\s*\)"
    r"|\b(?:in|whole|actual)\s+(?:whole\s+)?dollars\b"
)


def detect_unit(text: str) -> UnitStatement:
    """Find the unit statement. Two different monetary units in one note are ambiguous."""
    shares_spans = [(m.start(), m.end(), m.group(1).lower()) for m in _SHARES_UNIT.finditer(text)]
    shares_mult = _MULT[shares_spans[0][2]] if shares_spans else None
    found: list[tuple[int, int, Decimal]] = []
    for m in _MONEY_UNIT.finditer(text):
        if any(s <= m.start() < e for s, e, _ in shares_spans):
            continue
        word = (m.group(1) or "").lower()
        if word:
            mult = _MULT[word]
        elif "dollar" in m.group(0).lower():
            mult = Decimal(1)
        else:
            mult = _MULT["thousands"]
        found.append((m.start(), m.end(), mult))
    distinct = {f[2] for f in found}
    if len(distinct) > 1:
        return UnitStatement(None, found[0][0], found[0][1], shares_mult, ambiguous=True)
    if not found:
        return UnitStatement(None, None, None, shares_mult)
    return UnitStatement(found[0][2], found[0][0], found[0][1], shares_mult)


# ------------------------------------------------------------------------------------ values
# A number (optionally in parentheses, optionally with a dollar sign) or a dash. A number followed
# by a percent sign is a rate, not an amount, so it never matches.
_NUM = r"\(?\s*\$?\s*\(?\s*\d[\d,]*+(?:\.\d++)?+\s*\)?(?!\s*%)"
_DASH = r"[—–]|(?<![\w.,$(])-(?![\w.,$(])"
_VALUE = re.compile(f"{_NUM}|{_DASH}")
_FOOTNOTE = re.compile(r"^\(\d\)$")


@dataclass(frozen=True)
class _Token:
    raw: str
    start: int  # offsets into the whole note
    end: int


@dataclass(frozen=True)
class _ParsedValue:
    number: Decimal | None  # None for a dash or an unreadable token
    dash: bool
    problem: str | None = None


def _parse_token(raw: str) -> _ParsedValue:
    text = raw.strip()
    if text in ("—", "–", "-"):
        return _ParsedValue(None, True)
    if text.count("(") != text.count(")"):
        return _ParsedValue(None, False, "unbalanced parenthesis")
    negative = "(" in text
    digits = re.sub(r"[^\d.]", "", text.replace(",", ""))
    try:
        number = Decimal(digits)
    except InvalidOperation:
        return _ParsedValue(None, False, "unreadable number")
    return _ParsedValue(-number if negative else number, False)


@dataclass(frozen=True)
class NoteRow:
    """One row of a maturity table. `value` is scaled by the unit statement and is None for a
    dash, a missing unit or an unreadable cell; `raw_value` is the number as printed."""

    key: str | None  # bucket: year_1..year_5, thereafter, total, ...; None when unlabeled
    label: str
    value: Decimal | None
    raw_value: Decimal | None
    raw: str
    dash: bool
    start: int
    end: int
    status: Status
    reasons: tuple[str, ...] = ()
    columns: tuple[str, ...] = ()  # every value token on the row, as printed


@dataclass(frozen=True)
class Schedule:
    kind: Literal["debt_maturity", "lease_maturity"]
    rows: tuple[NoteRow, ...]
    unit: UnitStatement
    status: Status
    reasons: tuple[str, ...] = ()

    def row(self, key: str) -> NoteRow | None:
        return next((r for r in self.rows if r.key == key), None)

    def amounts(self) -> dict[str, Decimal]:
        """Year and thereafter buckets that carry a value (a dash bucket is left out, not zero)."""
        return {
            r.key: r.value
            for r in self.rows
            if r.key
            and (r.key.startswith("year_") or r.key == "thereafter")
            and r.value is not None
        }


# ------------------------------------------------------------------------------------- lines
def _lines(text: str) -> Iterator[tuple[int, str]]:
    pos = 0
    for raw in text.split("\n"):
        yield pos, raw
        pos += len(raw) + 1


def _clean(raw: str) -> str:
    """Pipes and non-breaking spaces become spaces; length is unchanged so offsets stay valid."""
    return raw.replace("|", " ").replace("\xa0", " ")


def _tokens(clean: str, offset: int, begin: int = 0) -> list[_Token]:
    return [
        _Token(m.group(0), offset + m.start(), offset + m.end())
        for m in _VALUE.finditer(clean, begin)
    ]


def _drop_footnote_marker(tokens: list[_Token]) -> list[_Token]:
    if len(tokens) > 1 and _FOOTNOTE.match(tokens[0].raw.strip()):
        return tokens[1:]
    return tokens


def _trailing_cluster(clean: str, offset: int) -> tuple[str, list[_Token]]:
    """For a line with no known label: the run of value tokens at the end and the text before."""
    toks = _tokens(clean, offset)
    cluster: list[_Token] = []
    for tok in reversed(toks):
        if cluster:
            gap = clean[tok.end - offset : cluster[0].start - offset]
            if gap.strip(" \t$") != "":
                break
        cluster.insert(0, tok)
    if not cluster:
        return clean.strip(), []
    return clean[: cluster[0].start - offset].strip(), cluster


# ------------------------------------------------------------------------- maturity schedules
_MONTHS = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
_YEAR_LABEL = re.compile(
    rf"""(?ix)^[\s\W]*
    (?:(?:for\s+the\s+)?(?:fiscal\s+)?(?:years?\s+(?:ending|ended)|year)\s+)?
    (?:{_MONTHS}\s+\d{{1,2}},?\s+)?
    (?P<year>(?:19|20)\d{{2}})\b"""
)
_YEAR_N = re.compile(r"(?i)^[\s\W]*(?:year|yr)\s+(?P<n>[1-5])\b")
_NEXT_YEAR = re.compile(
    r"(?i)^[\s\W]*(?:next\s+twelve\s+months|next\s+12\s+months|within\s+one\s+year|one\s+year"
    r"|due\s+in\s+one\s+year)"
)
_THEREAFTER = re.compile(r"(?i)^[\s\W]*(?:thereafter|later\s+years|beyond|after\s+(?:19|20)\d{2})")
_DEBT_DISCOUNT = re.compile(
    r"(?i)^[\s\W]*(?:less:?\s*)?(?:unamorti[sz]ed|discounts?\b|debt\s+issuance|deferred\s+financing)"
)
_LEASE_INTEREST = re.compile(
    r"(?i)^[\s\W]*(?:less:?\s*)?(?:imputed\s+interest|interest\b|amount\s+representing\s+interest"
    r"|present\s+value\s+discount)"
)
_LEASE_PV = re.compile(
    r"(?i)^[\s\W]*(?:present\s+value\b|total\s+(?:operating\s+|finance\s+)?lease\s+liabilit"
    r"|lease\s+liabilit)"
)
_TOTAL = re.compile(r"(?i)^[\s\W]*total\b")


def _classify(clean: str, kind: str) -> tuple[str, int] | None:
    """The bucket a row label names and where the label's keyword ends, or None."""
    checks: list[tuple[re.Pattern[str], str]] = []
    if kind == "lease_maturity":
        checks += [(_LEASE_INTEREST, "imputed_interest"), (_LEASE_PV, "present_value")]
    else:
        checks += [(_DEBT_DISCOUNT, "discount")]
    checks += [(_YEAR_N, "year_n"), (_NEXT_YEAR, "year_1"), (_THEREAFTER, "thereafter")]
    checks += [(_YEAR_LABEL, "calendar_year"), (_TOTAL, "total")]
    for pattern, key in checks:
        m = pattern.match(clean)
        if m:
            if key == "year_n":
                return f"year_{m.group('n')}", m.end()
            if key == "calendar_year":
                return f"calendar:{m.group('year')}", m.end()
            return key, m.end()
    return None


def _scaled(raw: Decimal | None, unit: UnitStatement) -> Decimal | None:
    if raw is None or unit.multiplier is None:
        return None
    return raw * unit.multiplier


def _unit_reason(unit: UnitStatement) -> str | None:
    if unit.ambiguous:
        return "unit ambiguous: the note states more than one monetary unit"
    if unit.multiplier is None:
        return "unit not stated: figures are not scaled"
    return None


def _build_row(
    key: str | None,
    clean: str,
    offset: int,
    label_end: int,
    tokens: list[_Token],
    unit: UnitStatement,
    extra_reasons: Sequence[str] = (),
) -> NoteRow:
    label = clean[:label_end].strip() if label_end else ""
    reasons = list(extra_reasons)
    if key is None:
        reasons.append("bucket unlabeled")
    unit_reason = _unit_reason(unit)
    if unit_reason:
        reasons.append(unit_reason)
    raw_value: Decimal | None = None
    dash = False
    if len(tokens) == 1:
        parsed = _parse_token(tokens[0].raw)
        raw_value, dash = parsed.number, parsed.dash
        if parsed.problem:
            reasons.append(parsed.problem)
    else:
        reasons.append(f"{len(tokens)} value columns: the amount column is not identified")
    start = offset + len(clean) - len(clean.lstrip())
    value = _scaled(raw_value, unit)
    if dash:
        reasons.append("dash: reported as nil and kept null, not zero")
    blocking = [r for r in reasons if not r.startswith("dash:")]
    return NoteRow(
        key,
        label,
        value,
        raw_value,
        " ".join(t.raw.strip() for t in tokens),
        dash,
        start,
        tokens[-1].end,
        "review" if blocking else "extracted",
        tuple(reasons),
        tuple(t.raw.strip() for t in tokens),
    )


def _extract_schedule(text: str, kind: Literal["debt_maturity", "lease_maturity"]) -> Schedule:
    unit = detect_unit(text)
    parsed: list[tuple[int, NoteRow, bool]] = []  # (line index, row, recognised)
    for idx, (offset, raw) in enumerate(_lines(text)):
        clean = _clean(raw)
        hit = _classify(clean, kind)
        if hit is not None:
            key, label_end = hit
            toks = _drop_footnote_marker(_tokens(clean, offset, label_end))
            if not toks:
                continue
            first = toks[0].start - offset
            parsed.append((idx, _build_row(key, clean, offset, first, toks, unit), True))
            continue
        label, cluster = _trailing_cluster(clean, offset)
        if cluster and re.search(r"[A-Za-z]", label):
            cluster = _drop_footnote_marker(cluster)
            label_end = cluster[0].start - offset
            parsed.append((idx, _build_row(None, clean, offset, label_end, cluster, unit), False))
    recognised = [i for i, _, ok in parsed if ok]
    rows: list[NoteRow] = []
    if recognised:
        lo, hi = min(recognised), max(recognised)
        rows = [r for i, r, _ in parsed if lo <= i <= hi]
    rows = _assign_years(rows)
    rows = _relabel_totals(rows)
    reasons: list[str] = []
    if not rows:
        reasons.append("no schedule rows found")
    else:
        if _unit_reason(unit):
            reasons.append(str(_unit_reason(unit)))
        if not any(r.key == "total" for r in rows):
            reasons.append("total missing")
        if kind == "lease_maturity":
            for need in ("imputed_interest", "present_value"):
                if not any(r.key == need for r in rows):
                    reasons.append(f"{need.replace('_', ' ')} missing")
        if not any(r.key and r.key.startswith("year_") for r in rows):
            reasons.append("no year buckets found")
        if any(r.status == "review" for r in rows):
            reasons.append("one or more rows need review")
    return Schedule(kind, tuple(rows), unit, "review" if reasons else "extracted", tuple(reasons))


def _replace_row(row: NoteRow, key: str | None, extra: Sequence[str] = ()) -> NoteRow:
    reasons = (*row.reasons, *extra)
    blocking = [r for r in reasons if not r.startswith("dash:")]
    return NoteRow(
        key,
        row.label,
        row.value,
        row.raw_value,
        row.raw,
        row.dash,
        row.start,
        row.end,
        "review" if blocking else "extracted",
        reasons,
        row.columns,
    )


def _assign_years(rows: list[NoteRow]) -> list[NoteRow]:
    """Calendar-year labels become year_1.. in order, only when the years are consecutive."""
    cal = sorted(
        {int(r.key.split(":")[1]) for r in rows if r.key and r.key.startswith("calendar:")}
    )
    if not cal:
        return rows
    consecutive = all(b - a == 1 for a, b in pairwise(cal)) and len(cal) <= 5
    out: list[NoteRow] = []
    for r in rows:
        if r.key and r.key.startswith("calendar:"):
            year = int(r.key.split(":")[1])
            if consecutive:
                out.append(_replace_row(r, f"year_{cal.index(year) + 1}"))
            else:
                out.append(
                    _replace_row(
                        r, None, ("years are not consecutive or exceed five: bucket unlabeled",)
                    )
                )
        else:
            out.append(r)
    return out


def _relabel_totals(rows: list[NoteRow]) -> list[NoteRow]:
    """With a subtotal before a discount line, only the last total row is the reported total."""
    idx = [i for i, r in enumerate(rows) if r.key == "total"]
    if len(idx) < 2:
        return rows
    out = list(rows)
    for i in idx[:-1]:
        out[i] = NoteRow(
            "subtotal",
            rows[i].label,
            rows[i].value,
            rows[i].raw_value,
            rows[i].raw,
            rows[i].dash,
            rows[i].start,
            rows[i].end,
            rows[i].status,
            rows[i].reasons,
            rows[i].columns,
        )
    return out


def extract_debt_maturities(text: str) -> Schedule:
    """Debt maturity buckets (year 1..5, thereafter), a reported total and any discount line."""
    return _extract_schedule(text, "debt_maturity")


def extract_lease_maturities(text: str) -> Schedule:
    """Lease maturity buckets, total undiscounted payments, imputed interest, present value."""
    return _extract_schedule(text, "lease_maturity")


# ------------------------------------------------------------------------------- repurchases
_PERIOD = re.compile(
    rf"""(?ix)^[\s\W]*(?P<label>
    (?:(?:for\s+the\s+)?(?:fiscal\s+)?(?:year|quarter|three\s+months|twelve\s+months)s?
        \s+(?:ending|ended)\s+)?
    (?:
       {_MONTHS}\s+\d{{1,2}}(?:,?\s*(?:19|20)\d{{2}})?\s*(?:[-–—]|to|through)\s*
           (?:{_MONTHS}\s+)?\d{{1,2}},?\s*(?:19|20)\d{{2}}
      |{_MONTHS}\s+\d{{1,2}},?\s*(?:19|20)\d{{2}}
      |{_MONTHS}\s+(?:19|20)\d{{2}}
      |q[1-4]\s*(?:fy)?\s*(?:19|20)\d{{2}}
      |(?:fiscal\s+)?(?:19|20)\d{{2}}
      |total
    ))"""
)
_CLASS = re.compile(
    r"(?i)\b(class\s+[A-C]\s+(?:common\s+stock|ordinary\s+shares|shares)|class\s+[A-C]"
    r"|ordinary\s+shares|common\s+stock|common\s+shares|american\s+depositary\s+shares|ADSs?)\b"
)
_MONTH_NUM = {
    m: i for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split(), 1)
}


@dataclass(frozen=True)
class RepurchaseRow:
    period_label: str  # as printed; "Total" for a total row
    period_start: dt.date | None
    period_end: dt.date | None
    shares: Decimal | None
    average_price: Decimal | None
    amount: Decimal | None
    share_class: str | None
    is_total: bool
    start: int
    end: int
    status: Status
    reasons: tuple[str, ...] = ()
    columns: tuple[str, ...] = ()


@dataclass(frozen=True)
class RepurchaseTable:
    rows: tuple[RepurchaseRow, ...]
    unit: UnitStatement
    share_class: str | None
    status: Status
    reasons: tuple[str, ...] = field(default_factory=tuple)


def _month(token: str) -> int:
    return _MONTH_NUM[token[:3].lower()]


def parse_period(label: str) -> tuple[dt.date | None, dt.date | None]:
    """Dates a period label names; (None, None) when it names none. A single date or a month
    gives an end only for a date, and the whole month for a month label."""
    text = label.strip()
    rng = re.search(
        rf"(?i)({_MONTHS})\s+(\d{{1,2}})(?:,?\s*((?:19|20)\d{{2}}))?\s*(?:[-–—]|to|through)"
        rf"\s*(?:({_MONTHS})\s+)?(\d{{1,2}}),?\s*((?:19|20)\d{{2}})",
        text,
    )
    try:
        if rng:
            m1, d1, y1, m2, d2, y2 = rng.groups()
            end_month = _month(m2) if m2 else _month(m1)
            end = dt.date(int(y2), end_month, int(d2))
            start = dt.date(int(y1 or y2), _month(m1), int(d1))
            return start, end
        single = re.search(rf"(?i)({_MONTHS})\s+(\d{{1,2}}),?\s*((?:19|20)\d{{2}})", text)
        if single:
            mon, day, year = single.groups()
            return None, dt.date(int(year), _month(mon), int(day))
        month_year = re.search(rf"(?i)({_MONTHS})\s+((?:19|20)\d{{2}})", text)
        if month_year:
            mon, year = month_year.groups()
            first = dt.date(int(year), _month(mon), 1)
            nxt = dt.date(first.year + (first.month == 12), first.month % 12 + 1, 1)
            return first, nxt - dt.timedelta(days=1)
    except ValueError:  # an impossible date such as February 31
        return None, None
    return None, None


def _split_cells(raw: str) -> list[str]:
    parts = raw.split("|") if "|" in raw else re.split(r"\s{2,}|\t", raw)
    return [p.strip() for p in parts if p.strip()]


def _classify_header(cell: str) -> str:
    c = cell.lower()
    if "average" in c or "price" in c:
        return "price"
    if "approximate" in c or "may yet" in c or "remaining" in c:
        return "ignore"
    if "part of" in c or "publicly announced" in c:
        return "ignore"
    if any(w in c for w in ("amount", "dollar value", "aggregate", "cost", "spent", "total value")):
        return "amount"
    if "shares" in c and ("purchased" in c or "repurchased" in c or "number" in c):
        return "shares"
    return "ignore"


def _header_columns(
    lines: list[tuple[int, str]], first_data: int, n_values: int
) -> list[str] | None:
    """Column roles from the nearest header line above the first data row, or None."""
    for _, raw in reversed(lines[:first_data]):
        low = raw.lower()
        if "shares" in low and ("price" in low or "amount" in low or "value" in low):
            cells = _split_cells(raw)
            if len(cells) == n_values + 1:
                cells = cells[1:]
            if len(cells) == n_values:
                return [_classify_header(c) for c in cells]
            return None
    return None


def extract_repurchases(text: str) -> RepurchaseTable:
    """Repurchase rows (period, shares, average price, amount). Columns are read only from a header
    line; without one the row is kept with its raw columns and sent to review."""
    unit = detect_unit(text)
    cls_match = _CLASS.search(text)
    share_class = cls_match.group(1) if cls_match else None
    lines = list(_lines(text))
    rows: list[RepurchaseRow] = []
    for idx, (offset, raw) in enumerate(lines):
        clean = _clean(raw)
        m = _PERIOD.match(clean)
        if not m:
            continue
        toks = _drop_footnote_marker(_tokens(clean, offset, m.end()))
        if not toks:
            continue
        label = m.group("label").strip()
        is_total = label.lower() == "total"
        roles = _header_columns(lines, idx, len(toks))
        reasons: list[str] = []
        shares = price = amount = None
        if roles is None:
            reasons.append("column roles not identified: no matching header line")
        else:
            reasons_unit = _unit_reason(unit)
            if reasons_unit:
                reasons.append(reasons_unit)
            for role, tok in zip(roles, toks, strict=True):
                parsed = _parse_token(tok.raw)
                if parsed.problem:
                    reasons.append(f"{role}: {parsed.problem}")
                    continue
                if parsed.dash:
                    reasons.append(f"{role}: dash kept as null, not zero")
                    continue
                num = parsed.number
                if role == "price":
                    price = num
                elif role == "amount":
                    amount = _scaled(num, unit)
                elif role == "shares" and num is not None:
                    if unit.shares_multiplier is not None:
                        shares = num * unit.shares_multiplier
                    elif unit.multiplier == 1:
                        shares = num
                    else:
                        reasons.append("share count scale not stated: shares left null")
        if not is_total and share_class is None:
            reasons.append("share class not stated")
        start, end = (None, None) if is_total else parse_period(label)
        if not is_total and end is None:
            reasons.append("period dates not readable from the label")
        blocking = [r for r in reasons if "dash kept" not in r]
        rows.append(
            RepurchaseRow(
                label,
                start,
                end,
                shares,
                price,
                amount,
                share_class,
                is_total,
                offset + len(clean) - len(clean.lstrip()),
                toks[-1].end,
                "review" if blocking else "extracted",
                tuple(reasons),
                tuple(t.raw.strip() for t in toks),
            )
        )
    reasons_all: list[str] = []
    if not rows:
        reasons_all.append("no repurchase rows found")
    else:
        unit_reason = _unit_reason(unit)
        if unit_reason:
            reasons_all.append(unit_reason)
        if any(r.status == "review" for r in rows):
            reasons_all.append("one or more rows need review")
    return RepurchaseTable(
        tuple(rows), unit, share_class, "review" if reasons_all else "extracted", tuple(reasons_all)
    )
