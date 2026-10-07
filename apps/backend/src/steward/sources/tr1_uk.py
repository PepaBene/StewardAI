"""UK TR-1 ("Notification of major holdings") parser for RNS announcements, a pilot for the
national voting-rights lane (P3.4).

A holder of voting rights in a UK-traded issuer notifies the issuer and the FCA when it crosses
a threshold; the issuer publishes the notice through an RNS ("Holding(s) in Company"). The notice
follows the FCA standard form, whose wording this parser expects:

    1a. Identity of the issuer ... to which voting rights are attached
    3.  Details of person subject to the notification obligation        Name
    4.  Full name of shareholder(s) (if different from 3.)              Name
    5.  Date on which the threshold was crossed or reached
    6.  Date on which Issuer notified
    7.  Total positions: "Resulting situation on the date on which threshold was crossed or
        reached" and "Position of previous notification", each as
        % of voting rights attached to shares | % through financial instruments | total % |
        total number of voting rights

UNVERIFIED: the template wording, the table layout of the plain text and the footnote markers
above are written from memory of the FCA form and have NOT been checked against live RNS
announcements; the fixtures in the tests are written to that wording, not captured. Retrieval of
announcement bodies is as unproven as for the PDMR adapter (see `sources.pdmr_uk`), so the
standing status is `access_unverified` until a notice has been parsed from a real announcement.

Rules kept here: `known_at` is the announcement time the caller passes (never the crossing date,
which can be days earlier); a field that cannot be read is None, never zero; a notice whose
resulting position states no percent is kept as a typed gap (`gap`), not dropped and not
guessed; the form's own total (shares plus instruments) is stored as stated and the two parts are
never summed here. The threshold crossed is not stated on the form: `thresholds_crossed` is
derived from the before and after totals under the UK rule of 3% and every whole percent above
it (also unverified), and is None when either total is unknown.
"""

import datetime as dt
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Literal

from steward.sources.base import AccessResult, AccessState

type Gap = Literal["no_percent_stated", "position_row_unreadable"]

_HEADLINE = re.compile(
    r"holding\(s\) in company|\bform tr-?1\b|notification of major holdings?", re.IGNORECASE
)
_FOOTNOTE = re.compile(r"^(?:i|ii|iii|iv|v|vi|vii|viii|ix|x)\s+(?=\S)")
_VALUE = re.compile(r"\bn/?a\b|\bnil\b|\d[\d,]*(?:\.\d+)?%?", re.IGNORECASE)
_MONTHS = {
    m: i
    for i, names in enumerate(
        (
            ("jan", "january"),
            ("feb", "february"),
            ("mar", "march"),
            ("apr", "april"),
            ("may",),
            ("jun", "june"),
            ("jul", "july"),
            ("aug", "august"),
            ("sep", "sept", "september"),
            ("oct", "october"),
            ("nov", "november"),
            ("dec", "december"),
        ),
        start=1,
    )
    for m in names
}
_DATE = re.compile(r"(\d{1,2})(?:st|nd|rd|th)?[\s\-/]+([A-Za-z]+)[\s\-/,]+(\d{4})")


def is_tr1_announcement(headline: str) -> bool:
    """True for an RNS headline that is a major-holdings notice."""
    return bool(_HEADLINE.search(headline))


def tr1_access() -> AccessResult:
    """Standing status of the UK TR-1 pipeline: parser only, retrieval not demonstrated."""
    return AccessResult(
        AccessState.ACCESS_UNVERIFIED,
        "UK TR-1 notices: the FCA standard-form parser is implemented and tested on fixtures "
        "written to the expected wording; no announcement has been parsed from the live RNS "
        "stream, so the national voting-rights lane stays not covered.",
    )


@dataclass(frozen=True)
class Tr1Notice:
    holder: str | None
    holder_office: str | None
    shareholder_if_different: str | None
    issuer: str | None
    percent_shares_after: Decimal | None
    percent_instruments_after: Decimal | None
    percent_total_after: Decimal | None  # as the form states it
    voting_rights_after: Decimal | None
    percent_total_before: Decimal | None  # the previous notification, if it states one
    thresholds_crossed: tuple[int, ...] | None  # derived, see the module docstring
    direction: Literal["up", "down"] | None
    date_crossed: dt.date | None
    date_issuer_notified: dt.date | None
    known_at: dt.datetime  # the announcement time
    gap: Gap | None  # set when the resulting position has no usable percent


def _flat(text: str) -> str:
    return " ".join(text.replace(" | ", " ").replace("|", " ").split())


def _field(flat: str, label: str, stop: str) -> str | None:
    """The text after `label` up to `stop`, with a footnote marker and a leading colon removed."""
    match = re.search(label + r"\s*:?\s*(.*?)(?=" + stop + r"|$)", flat, re.IGNORECASE)
    if not match:
        return None
    value = " ".join(match.group(1).split()).strip(" :-")
    value = _FOOTNOTE.sub("", value).strip(" :-")
    return value or None


def _date(raw: str | None) -> dt.date | None:
    found = _DATE.search(raw or "")
    if not found:
        return None
    month = _MONTHS.get(found.group(2).lower())
    if month is None:
        return None
    try:
        return dt.date(int(found.group(3)), month, int(found.group(1)))
    except ValueError:
        return None


def _number(token: str) -> Decimal | None:
    """A stated number; 'N/A' and 'Nil' are not numbers, so they stay None."""
    cleaned = token.rstrip("%").replace(",", "")
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def _percent(token: str) -> Decimal | None:
    value = _number(token)
    return value if value is not None and Decimal(0) <= value <= Decimal(100) else None


def _row(flat: str, label: str, stop: str) -> list[str]:
    block = _field(flat, label, stop) or ""
    return [m.group(0) for m in _VALUE.finditer(block)]


def derive_thresholds(
    before: Decimal | None, after: Decimal | None
) -> tuple[tuple[int, ...] | None, Literal["up", "down"] | None]:
    """Whole-percent thresholds (3 and up) between two totals: a threshold n is reached or
    crossed upward when before < n <= after, and crossed downward when after < n <= before."""
    if before is None or after is None:
        return None, None
    up = tuple(n for n in range(3, 101) if before < n <= after)
    down = tuple(n for n in range(3, 101) if after < n <= before)
    if up:
        return up, "up"
    return down, "down" if down else None


def parse_tr1_notice(text: str, announced_at: dt.datetime) -> Tr1Notice | None:
    """Parse the form from the announcement's plain text; None when it is not a TR-1 notice.

    `announced_at` (timezone-aware) becomes `known_at`."""
    if announced_at.tzinfo is None:
        raise ValueError("announced_at must be timezone-aware")
    flat = _flat(text)
    if not re.search(r"major holdings|resulting situation on the date", flat, re.IGNORECASE):
        return None
    if not re.search(r"resulting situation on the date", flat, re.IGNORECASE):
        return None  # a headline or summary without the position table has no percent to read
    issuer = _field(
        flat,
        r"voting rights are attached",
        r"UK or Non-UK Issuer|\b1b\.|\b2\.\s+Reason",
    )
    issuer = re.sub(r"^issuer name\s*:?\s*", "", issuer or "", flags=re.IGNORECASE) or None
    part3 = (
        _field(flat, r"\b3\.\s+Details of person subject", r"\b4\.\s+Full name|\b5\.\s+Date on")
        or ""
    )
    holder = _field(part3, r"\bName", r"City and country|$^")
    office = _field(part3, r"City and country of registered office(?: \(if applicable\))?", r"$^")
    part4 = _field(flat, r"\b4\.\s+Full name of shareholder", r"\b5\.\s+Date on which") or ""
    other = _field(part4, r"(?:\(if different from 3\.?\)\s*)?(?:[ivx]+\s+)?Name", r"City and|$^")
    crossed = _field(
        flat, r"\b5\.\s+Date on which the threshold was crossed or reached", r"\b6\.\s+Date on"
    )
    notified = _field(flat, r"\b6\.\s+Date on which Issuer notified", r"\b7\.\s+Total|$^")
    after = _row(
        flat,
        r"Resulting situation on the date on which threshold was crossed or reached",
        r"Position of previous notification|\b8\.\s+Notified|$^",
    )
    previous = _row(
        flat,
        r"Position of previous notification(?: \(if applicable\))?",
        r"\b8\.\s+Notified|Notified details|$^",
    )
    shares = instruments = total = rights = None
    gap: Gap | None = None
    if len(after) >= 4:  # positional cells: fewer than four values cannot be assigned safely
        shares, instruments, total = (_percent(t) for t in after[:3])
        rights = _number(after[3])
        if total is None and shares is None and instruments is None:
            gap = "no_percent_stated"
    else:
        gap = "position_row_unreadable"
    before = _percent(previous[2]) if len(previous) >= 3 else None
    thresholds, direction = derive_thresholds(before, total)
    return Tr1Notice(
        holder=holder,
        holder_office=office,
        shareholder_if_different=other,
        issuer=issuer,
        percent_shares_after=shares,
        percent_instruments_after=instruments,
        percent_total_after=total,
        voting_rights_after=rights,
        percent_total_before=before,
        thresholds_crossed=thresholds,
        direction=direction,
        date_crossed=_date(crossed),
        date_issuer_notified=_date(notified),
        known_at=announced_at,
        gap=gap,
    )
