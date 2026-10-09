"""SEC Schedule 13D / 13G beneficial-ownership filings (structured XML).

Since December 2024 the SEC accepts Schedules 13D and 13G only as structured XML
(`edgarSubmission` with `headerData` and `formData`: `coverPageHeader`, one reporting-person
block per person, `items`). Earlier filings (form types "SC 13D", "SC 13G" and their /A
amendments) are free-form text or HTML with no structured data: they are reported as
unstructured by the collector, never parsed by guesswork.

Field names are matched by their local XML name and tolerate the variants of the 13D and
13G schemas (for instance `aggregateAmountOwned` and
`reportingPersonBeneficiallyOwnedAggregateNumberOfShares`). The fixtures are modelled on the
published schema description; this parser has not been run against a live filing.

Semantics worth keeping in mind (they drive the read model's limitations):

- percent_of_class is the percentage as reported, in percent points (6.8 = 6.8 %);
- each reporting person reports its own beneficial ownership, and the members of a group
  report overlapping shares, so rows are never summed; the filing-level stake is the largest
  single reporting-person figure, a lower bound for the group;
- a 13D/G describes control and intent over one class of an issuer's securities. It is a
  different channel from Form 13F positions and fund holdings and is never added to them.
"""

import datetime as dt
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from steward.sources.ciks import ten_digit_cik
from steward.sources.xml_guard import safe_fromstring

SCHEDULE13_FORMS = frozenset(
    {
        "SC 13D",
        "SC 13D/A",
        "SC 13G",
        "SC 13G/A",
        "SCHEDULE 13D",
        "SCHEDULE 13D/A",
        "SCHEDULE 13G",
        "SCHEDULE 13G/A",
    }
)


class Schedule13Error(ValueError):
    pass


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _find(node: ET.Element | None, names: tuple[str, ...]) -> ET.Element | None:
    """First descendant whose local name is one of `names` (in the order of `names`)."""
    if node is None:
        return None
    for name in names:
        for child in node.iter():
            if _local(child.tag) == name:
                return child
    return None


def _first_node(node: ET.Element, names: tuple[str, ...]) -> ET.Element:
    """The named descendant, else `node` itself (an Element with no children is falsy)."""
    found = _find(node, names)
    return node if found is None else found


def _text(node: ET.Element | None, *names: str) -> str | None:
    found = _find(node, names)
    if found is None or found.text is None:
        return None
    return found.text.strip() or None


def _number(node: ET.Element | None, *names: str) -> Decimal | None:
    raw = _text(node, *names)
    if raw is None:
        return None
    try:
        value = Decimal(raw.replace(",", "").rstrip("%").strip())
    except InvalidOperation:
        return None
    return value if value.is_finite() else None


def _non_negative(value: Decimal | None) -> Decimal | None:
    """A negative share count is a filing error: unknown, never stored (the tables reject it)."""
    return value if value is not None and value >= 0 else None


def _percent(value: Decimal | None) -> Decimal | None:
    """A reported percent outside 0-100 is a filing error: unknown, not clamped."""
    return value if value is not None and Decimal(0) <= value <= Decimal(100) else None


def _date(raw: str | None) -> dt.date | None:
    if not raw:
        return None
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(raw.strip(), fmt).date()
        except ValueError:
            continue
    return None


def _group_flag(raw: str | None) -> bool | None:
    """Item 2 check box: (a) the person is a member of a group, (b) it is not."""
    if raw is None:
        return None
    value = raw.strip().lower()
    if value in {"a", "true", "1", "yes"}:
        return True
    if value in {"b", "false", "0", "no"}:
        return False
    return None


def _normalise_name(name: str) -> str:
    return re.sub(r"[^A-Z0-9]+", " ", name.upper()).strip()


@dataclass(frozen=True)
class ReportingPerson:
    cik: str | None
    name: str
    member_of_group: bool | None
    citizenship: str | None
    sole_voting: Decimal | None
    shared_voting: Decimal | None
    sole_dispositive: Decimal | None
    shared_dispositive: Decimal | None
    aggregate_shares: Decimal | None
    percent_of_class: Decimal | None
    person_type: str | None

    @property
    def key(self) -> str:
        """Stable identity of the person across amendments: the CIK, else the normalised name."""
        return f"cik:{self.cik}" if self.cik else f"name:{_normalise_name(self.name)}"


@dataclass(frozen=True)
class Schedule13Filing:
    schedule: str  # "13D" or "13G"
    is_amendment: bool
    amendment_no: int | None
    submission_type: str | None
    issuer_cik: str | None
    issuer_cusip: str | None
    issuer_name: str | None
    class_title: str | None
    event_date: dt.date | None
    filer_cik: str | None
    persons: list[ReportingPerson]
    # Item 4 (purpose of the transaction), 13D only; a 13G is passive by definition.
    intent_text: str | None
    # Item 7 (material filed as exhibits), 13D only: (label, description) as the filing lists them.
    exhibits: tuple[tuple[str, str | None], ...] = ()

    @property
    def stake_shares(self) -> Decimal | None:
        return stake_of(self.persons)[0]

    @property
    def stake_percent(self) -> Decimal | None:
        return stake_of(self.persons)[1]


def stake_of(persons: list[ReportingPerson]) -> tuple[Decimal | None, Decimal | None]:
    """Filing-level stake: the largest single reporting-person figure.

    Members of a group report overlapping beneficial ownership, so their figures are never
    added; the largest is a lower bound of what the group holds. Unknown stays None.
    """
    shares = [p.aggregate_shares for p in persons if p.aggregate_shares is not None]
    percents = [p.percent_of_class for p in persons if p.percent_of_class is not None]
    return (max(shares) if shares else None, max(percents) if percents else None)


def normalise_cusip(raw: str | None) -> str | None:
    if raw is None:
        return None
    cusip = re.sub(r"[^0-9A-Za-z]", "", raw).upper()
    return cusip if len(cusip) == 9 else None


def _person(node: ET.Element) -> ReportingPerson | None:
    name = _text(node, "reportingPersonName", "nameOfReportingPerson")
    if not name:
        return None
    cik = _text(node, "reportingPersonCIK", "reportingPersonCik")
    return ReportingPerson(
        cik=ten_digit_cik(cik),
        name=name,
        member_of_group=_group_flag(_text(node, "memberOfGroup", "memberGroup")),
        citizenship=_text(node, "citizenshipOrOrganization"),
        sole_voting=_non_negative(_number(node, "soleVotingPower")),
        shared_voting=_non_negative(_number(node, "sharedVotingPower")),
        sole_dispositive=_non_negative(_number(node, "soleDispositivePower")),
        shared_dispositive=_non_negative(_number(node, "sharedDispositivePower")),
        aggregate_shares=_non_negative(
            _number(
                node,
                "aggregateAmountOwned",
                "reportingPersonBeneficiallyOwnedAggregateNumberOfShares",
            )
        ),
        percent_of_class=_percent(_number(node, "percentOfClass", "classPercent")),
        person_type=_text(node, "typeOfReportingPerson"),
    )


def _intent(root: ET.Element) -> str | None:
    item4 = _find(root, ("item4",))
    if item4 is None:
        return None
    purpose = _text(item4, "purposeOfTransaction", "transactionPurpose")
    if purpose:
        return " ".join(purpose.split())
    flat = " ".join(" ".join(t.strip() for t in item4.itertext() if t.strip()).split())
    return flat or None


_EXHIBIT = re.compile(r"(?=\b(?:Exhibit|Ex\.)\s*\d+(?:\.\d+)?\b)", re.I)
_DASHES = re.escape("-" + chr(0x2013) + chr(0x2014))  # hyphen, en dash, em dash
_EXHIBIT_HEAD = re.compile(
    rf"^((?:Exhibit|Ex\.)\s*\d+(?:\.\d+)?)\s*[:{_DASHES}.]?\s*(.*)$", re.I | re.S
)


def parse_exhibits(text: str | None) -> tuple[tuple[str, str | None], ...]:
    """Exhibit entries from Item 7 text: 'Exhibit 99.1 - Joint Filing Agreement' gives
    ('Exhibit 99.1', 'Joint Filing Agreement'). Nothing is invented: text with no exhibit label
    yields no rows, and a label with no description keeps a null description."""
    if not text:
        return ()
    out: list[tuple[str, str | None]] = []
    for chunk in _EXHIBIT.split(" ".join(text.split())):
        m = _EXHIBIT_HEAD.match(chunk.strip())
        if m:
            description = m.group(2).strip(" .;") or None
            out.append((m.group(1).strip(), description))
    return tuple(out)


def _exhibits(root: ET.Element) -> tuple[tuple[str, str | None], ...]:
    item7 = _find(root, ("item7",))
    if item7 is None:
        return ()
    return parse_exhibits(" ".join(t.strip() for t in item7.itertext() if t.strip()))


def parse_schedule13_xml(xml: bytes, form: str | None = None) -> Schedule13Filing:
    """Parse the structured XML of a Schedule 13D/G or amendment.

    `form` is the submissions-API form type; it is used when the XML omits the submission
    type. Raises Schedule13Error for a document that is not a Schedule 13D/G.
    """
    root = safe_fromstring(xml, Schedule13Error)

    namespace = root.tag.lower()
    submission_type = _text(root, "submissionType") or form
    marker = f"{namespace} {(submission_type or '').lower()}"
    if "13d" in marker:
        schedule = "13D"
    elif "13g" in marker:
        schedule = "13G"
    elif _find(root, ("schedule13D",)) is not None:
        schedule = "13D"
    elif _find(root, ("schedule13G",)) is not None:
        schedule = "13G"
    else:
        raise Schedule13Error("document is not a Schedule 13D or 13G")

    header = _first_node(root, ("coverPageHeader",))
    issuer = _first_node(header, ("issuerInfo",))
    amendment_raw = _text(header, "amendmentNo")
    amendment_no = int(amendment_raw) if amendment_raw and amendment_raw.isdigit() else None
    is_amendment = bool((submission_type or "").upper().endswith("/A")) or bool(amendment_no)
    persons: list[ReportingPerson] = []
    seen: set[str] = set()
    for node in root.iter():
        if _local(node.tag) in {"reportingPersonInfo", "coverPageHeaderReportingPersonDetails"}:
            person = _person(node)
            if person is not None and person.key not in seen:
                seen.add(person.key)
                persons.append(person)
    if not persons:
        raise Schedule13Error("no reporting person found")
    issuer_cik = _text(issuer, "issuerCIK", "issuerCik")
    filer = _find(_find(root, ("headerData",)), ("cik",))
    filer_cik = ten_digit_cik(filer.text if filer is not None else None)
    return Schedule13Filing(
        schedule=schedule,
        is_amendment=is_amendment,
        amendment_no=amendment_no,
        submission_type=submission_type,
        issuer_cik=ten_digit_cik(issuer_cik),
        issuer_cusip=normalise_cusip(_text(issuer, "issuerCUSIP", "issuerCusip")),
        issuer_name=_text(issuer, "issuerName", "nameOfIssuer"),
        class_title=_text(header, "securitiesClassTitle", "titleOfClassOfSecurities"),
        event_date=_date(
            _text(header, "dateOfEvent", "eventDateRequiresFilingThisStatement", "dateOfEventDate")
        ),
        filer_cik=filer_cik,
        persons=persons,
        intent_text=_intent(root) if schedule == "13D" else None,
        exhibits=_exhibits(root) if schedule == "13D" else (),
    )
