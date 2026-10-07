"""P5.2: pull customer/supplier concentration claims out of a filing note, structure first.

Input is the plain text of one note (a customer-concentration note, a segment note or a material
agreements passage). Output is a list of DRAFT claims, every one `suggested`: the extractor never
confirms anything. Each draft keeps the character offsets of the sentence or table row it came from,
so a reviewer can open the exact passage.

Rules, in order of confidence:
  1. A table row  `Customer A | 23% | 19%` under a header of years gives one draft per year column.
  2. A sentence  "Customer A accounted for 23% of total revenue in fiscal 2025" gives a named or
     anonymous draft; "two customers accounted for 45% of revenue" gives a group draft.
  3. A sentence that is negated, hypothetical, historical or about a third party yields NO draft;
     it is returned in `skipped` with the reason, never dropped silently.
Contract terms (exclusivity, a minimum purchase, termination) are attached only when the same
sentence states them; nothing is inferred.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal

from steward.disclosure.dependencies import screen_passage
from steward.disclosure.entities import _ANONYMOUS

_NUM = r"(\d{1,3}(?:\.\d+)?)\s?%"
_WORDS = {
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}
_DENOM = (
    (re.compile(r"accounts? receivable|receivables", re.I), "receivables"),
    (re.compile(r"cost of (revenue|sales|goods)|purchases", re.I), "purchases"),
    (re.compile(r"segment (net )?(revenue|sales)", re.I), "segment_revenue"),
    (re.compile(r"(total |net |consolidated )?(revenues?|net sales|sales)", re.I), "total_revenue"),
)
_PERIOD = re.compile(r"\b(?:fiscal(?: year)?|FY)\s?(20\d\d)\b|\bin (20\d\d)\b|\b(20\d\d)\b", re.I)
# A sentence ends at ". " or "; " or a newline, but not after a company-form abbreviation, so
# "Apple Inc. accounted for 12%" stays one sentence.
_SENTENCE = re.compile(
    r"(.+?)(?:(?<!\bInc)(?<!\bCorp)(?<!\bLtd)(?<!\bCo)(?<!\bLLC)(?<!\bPLC)(?<!\bNo)[.;](?=\s|$)"
    r"|\n|$)",
    re.S,
)
_SUBJECT = re.compile(
    r"^\s*(?P<who>(?:Customer|Supplier|Distributor|Partner|Client) [A-Z0-9]|"
    r"(?:[A-Z][\w&.'-]*(?: [A-Z][\w&.'-]*){0,4}))\s+(?:accounted for|represented|comprised|"
    r"contributed|made up)\s+(?:approximately |about |roughly )?" + _NUM,
)
_GROUP = re.compile(
    r"(?P<n>two|three|four|five|six|seven|eight|nine|ten|\d+)\s+(?:largest\s+|significant\s+|"
    r"major\s+)?(?P<kind>customers?|suppliers?|distributors?)\s+(?:together\s+)?(?:accounted for|"
    r"represented|comprised)\s+(?:approximately |about )?" + _NUM,
    re.I,
)
_TERMS = (
    ("exclusivity", re.compile(r"\bexclusive(ly)?\b|\bexclusivity\b", re.I)),
    ("minimum_purchase", re.compile(r"minimum (purchase|commitment|volume)s?", re.I)),
    ("termination", re.compile(r"\bterminat(e|ion|ed)\b|\bnotice of non-?renewal\b", re.I)),
)
_FALSE_SUBJECT = {"The Company", "We", "Our", "The", "Revenue", "Revenues", "Sales", "Net Sales"}


@dataclass(frozen=True)
class Draft:
    shape: str  # named | anonymous | group
    relationship: str  # customer | supplier
    name: str | None  # only for a named claim
    label: str | None  # an anonymous or group claim's own wording
    count: int | None
    share: Decimal  # a fraction
    denominator: str | None
    fiscal_period: str | None
    start: int  # offsets into the input text
    end: int
    passage: str
    terms: tuple[str, ...] = ()
    hints: tuple[str, ...] = ()


@dataclass(frozen=True)
class Skipped:
    start: int
    end: int
    passage: str
    reason: str


@dataclass
class Extraction:
    drafts: list[Draft] = field(default_factory=list)
    skipped: list[Skipped] = field(default_factory=list)


def _denominator(text: str) -> str | None:
    for rx, name in _DENOM:
        if rx.search(text):
            return name
    return None


def _period(text: str) -> str | None:
    m = _PERIOD.search(text)
    if not m:
        return None
    year = next(g for g in m.groups() if g)
    return f"FY{year}"


def _relationship(text: str, subject: str | None) -> str:
    if re.search(r"supplier|vendor|purchases|cost of", text, re.I) or (
        subject and subject.lower().startswith(("supplier", "vendor"))
    ):
        return "supplier"
    return "customer"


def _fraction(raw: str) -> Decimal | None:
    value = Decimal(raw) / 100
    return value if Decimal(0) <= value <= Decimal(1) else None


def _terms(text: str) -> tuple[str, ...]:
    return tuple(name for name, rx in _TERMS if rx.search(text))


def _from_sentence(text: str, start: int, end: int, out: Extraction) -> None:
    sentence = text[start:end].strip()
    if not sentence:
        return
    offset = start + (len(text[start:end]) - len(text[start:end].lstrip()))
    stop = offset + len(sentence)
    hints = screen_passage(sentence)
    group = _GROUP.search(sentence)
    subject = _SUBJECT.match(sentence)
    if not group and not subject:
        # Not a draft, but a screened sentence about a percentage is shown, never dropped quietly.
        if hints and "%" in sentence and re.search(r"accounted for|represent|comprised", sentence):
            out.skipped.append(Skipped(offset, stop, sentence, "screened: " + ", ".join(hints)))
        return
    if hints:
        out.skipped.append(Skipped(offset, stop, sentence, "screened: " + ", ".join(hints)))
        return
    if group:
        count = _WORDS.get(group["n"].lower()) or int(group["n"])
        share = _fraction(group.group(3))
        if share is None or count < 2:
            out.skipped.append(
                Skipped(offset, stop, sentence, "share out of range or count below 2")
            )
            return
        label = f"{group['n']} {group['kind']}"
        out.drafts.append(
            Draft(
                "group",
                _relationship(sentence, group["kind"]),
                None,
                label,
                count,
                share,
                _denominator(sentence),
                _period(sentence),
                offset,
                stop,
                sentence,
                _terms(sentence),
            )
        )
        return
    assert subject is not None
    who = subject["who"].strip()
    share = _fraction(subject.group(2))
    if share is None or who in _FALSE_SUBJECT or who.split()[0] in {"The", "We", "Our"}:
        out.skipped.append(Skipped(offset, stop, sentence, "no usable counterparty or share"))
        return
    anonymous = bool(_ANONYMOUS.match(who))
    out.drafts.append(
        Draft(
            "anonymous" if anonymous else "named",
            _relationship(sentence, who),
            None if anonymous else who,
            who if anonymous else None,
            None,
            share,
            _denominator(sentence),
            _period(sentence),
            offset,
            stop,
            sentence,
            _terms(sentence),
        )
    )


def _from_table(text: str, out: Extraction) -> None:
    """Rows of `name | 23% | 19%` under a `| 2025 | 2024 |` header: one draft per year column."""
    years: list[str] = []
    pos = 0
    for line in text.splitlines(keepends=True):
        start, pos = pos, pos + len(line)
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2:
            continue
        found = [re.fullmatch(r"(?:FY\s?)?(20\d\d)", c) for c in cells[1:]]
        if all(found) and found:
            years = [f"FY{m.group(1)}" for m in found if m]
            continue
        pct = [re.fullmatch(_NUM, c) for c in cells[1:]]
        if not years or len(pct) != len(years) or not all(pct) or not cells[0]:
            continue
        who = cells[0]
        if who.lower() in {"total", "other", "all other", "all other customers"}:
            continue
        anonymous = bool(_ANONYMOUS.match(who))
        for year, m in zip(years, pct, strict=True):
            assert m is not None
            share = _fraction(m.group(1))
            if share is None:
                continue
            out.drafts.append(
                Draft(
                    "anonymous" if anonymous else "named",
                    "customer",
                    None if anonymous else who,
                    who if anonymous else None,
                    None,
                    share,
                    _denominator(text),
                    year,
                    start,
                    pos,
                    line.strip(),
                    (),
                )
            )


def extract(text: str) -> Extraction:
    """Drafts and skipped passages for one note. Table rows are read first; a sentence that repeats
    a table row is not drafted twice."""
    out = Extraction()
    _from_table(text, out)
    covered = [(d.start, d.end) for d in out.drafts]
    for m in _SENTENCE.finditer(text):
        if not m.group(1).strip() or any(a <= m.start() < b for a, b in covered):
            continue
        _from_sentence(text, m.start(), m.end(), out)
    out.drafts.sort(key=lambda d: (d.start, d.fiscal_period or ""))
    return out
