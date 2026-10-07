"""P3.5: corroborate a reported stake (13D/G or a national voting-rights notice) against
legal-entity control data: Companies House persons with significant control (PSC) and the GLEIF
accounting-consolidation parent.

This is separate control data, never an ownership lane and never added to one. Each comparison
is one of three typed verdicts:

    agree           the reported percent lies inside the PSC band, or a reported majority stake
                    is held by the GLEIF consolidation parent
    conflict        the two sources contradict each other; the conflict is KEPT for review and
                    never resolved by preferring one source
    not_comparable  the sources describe different things (other basis, no percent, no holder
                    match, a record that ended); the reason says which

A PSC figure is a band (25-50%, 50-75%, 75-100%), never a point value, so an agreement means only
"inside the band". Dates travel with the verdict because the two sources are measured on
different days. Records first known after `as_of` are ignored (point-in-time).

Unverified against live data: the PSC `natures_of_control` strings below follow the Companies
House PSC statement vocabulary as recalled and the lower-bound-exclusive reading of the bands
("more than 25% but not more than 50%") has not been checked against a live response. Companies
that are admitted to trading on a UK regulated market are, to be confirmed, generally exempt from
the PSC regime, so PSC data mostly exists for unlisted holders and subsidiaries. GLEIF level 2
states accounting consolidation, not a percentage, so it can corroborate a majority stake but
never gives a band.
"""

import datetime as dt
import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

type Verdict = Literal["agree", "conflict", "not_comparable"]
type Basis = Literal["shares", "voting"]

# The PSC bands, as (low, high): low exclusive, high inclusive.
_PSC_NATURE = re.compile(
    r"^(?P<kind>ownership-of-shares|voting-rights)-(?P<low>\d{2})-to-(?P<high>\d{2,3})-percent"
    r"(?P<tail>-.*)?$"
)
_KIND_BASIS: dict[str, Basis] = {"ownership-of-shares": "shares", "voting-rights": "voting"}
_SUFFIXES = re.compile(
    r"\b(?:plc|ltd|limited|llp|lp|inc|incorporated|corp|corporation|co|company|holdings?|"
    r"group|sa|ag|nv|bv|gmbh|llc)\b"
)


@dataclass(frozen=True)
class PercentBand:
    """A percentage range. Never collapsed to a point value."""

    low: Decimal
    high: Decimal
    low_inclusive: bool = False  # PSC bands are "more than low, up to and including high"

    def contains(self, percent: Decimal) -> bool:
        above = percent >= self.low if self.low_inclusive else percent > self.low
        return above and percent <= self.high

    def label(self) -> str:
        return f"{self.low}-{self.high}%"


@dataclass(frozen=True)
class PscRecord:
    """One person with significant control of a company, as stored (nothing is inferred)."""

    company_number: str
    name: str
    natures_of_control: tuple[str, ...]
    notified_on: dt.date | None
    ceased_on: dt.date | None
    known_at: dt.datetime  # when we retrieved it
    identification_lei: str | None = None  # only when a corporate PSC states a registration id


@dataclass(frozen=True)
class GleifParent:
    """The GLEIF level-2 parent relationship of one child entity, or its stated exception."""

    child_lei: str
    parent_lei: str | None
    parent_name: str | None
    relationship: str | None  # "IS_DIRECTLY_CONSOLIDATED_BY" / "IS_ULTIMATELY_CONSOLIDATED_BY"
    reporting_exception: str | None  # "NATURAL_PERSONS", "NON_CONSOLIDATING", "NO_KNOWN_PERSON"...
    known_at: dt.datetime


@dataclass(frozen=True)
class ReportedStake:
    """A stake taken from a 13D/G or a national notice, with what it measures."""

    holder_name: str
    holder_lei: str | None
    percent: Decimal | None  # null is never zero
    basis: Basis | None  # shares (beneficial ownership of the class) or voting rights
    as_of: dt.date | None


@dataclass(frozen=True)
class ControlComparison:
    source: Literal["psc", "gleif"]
    verdict: Verdict
    reason: str
    band: PercentBand | None  # PSC only; a band, not a point
    reported_percent: Decimal | None
    reported_as_of: dt.date | None
    control_as_of: dt.date | None  # the PSC notification date; None when the source gives none
    matched_name: str | None
    needs_review: bool = field(default=False)  # True for every conflict


def normalise_name(name: str) -> str:
    """Name key for a holder match: case, punctuation and company-form words removed. A name
    match is a lead, not an identity, and the reason text says so."""
    lowered = re.sub(r"[^a-z0-9 ]+", " ", name.lower().replace("&", " and "))
    return " ".join(_SUFFIXES.sub(" ", lowered).split())


def parse_nature(nature: str) -> tuple[Basis, PercentBand] | None:
    """The basis and band of a PSC nature-of-control string; None for natures that carry no
    percentage (right to appoint directors, significant influence)."""
    match = _PSC_NATURE.match(nature.strip().lower())
    if not match:
        return None
    low, high = Decimal(match["low"]), Decimal(match["high"])
    if low >= high or high > 100:
        return None
    return _KIND_BASIS[match["kind"]], PercentBand(low, high)


def _result(
    source: Literal["psc", "gleif"],
    verdict: Verdict,
    reason: str,
    reported: ReportedStake,
    *,
    band: PercentBand | None = None,
    control_as_of: dt.date | None = None,
    matched: str | None = None,
) -> ControlComparison:
    return ControlComparison(
        source,
        verdict,
        reason,
        band,
        reported.percent,
        reported.as_of,
        control_as_of,
        matched,
        needs_review=verdict == "conflict",
    )


def compare_psc(
    reported: ReportedStake, pscs: list[PscRecord], as_of: dt.datetime
) -> ControlComparison:
    """Compare a reported percent with the PSC band of the same holder and basis."""
    if reported.percent is None:
        return _result("psc", "not_comparable", "the reported stake states no percent", reported)
    if reported.basis is None:
        return _result(
            "psc",
            "not_comparable",
            "the reported stake does not say whether it measures shares or voting rights",
            reported,
        )
    known = [p for p in pscs if p.known_at <= as_of]
    key = normalise_name(reported.holder_name)
    named = [p for p in known if key and normalise_name(p.name) == key]
    if reported.holder_lei:
        named += [
            p for p in known if p.identification_lei == reported.holder_lei and p not in named
        ]
    if not named:
        return _result(
            "psc",
            "not_comparable",
            "no PSC record is known for the reported holder (matched by name or LEI only)",
            reported,
        )
    # A PSC that ended before the reported date describes a past position.
    current = [
        p for p in named if not (p.ceased_on and reported.as_of and p.ceased_on <= reported.as_of)
    ]
    if not current:
        ended = max(p.ceased_on for p in named if p.ceased_on)
        return _result(
            "psc",
            "not_comparable",
            f"the PSC entry ended on {ended}, before the reported date",
            reported,
            control_as_of=ended,
            matched=named[0].name,
        )
    bands: list[tuple[PercentBand, PscRecord]] = []
    for record in current:
        for nature in record.natures_of_control:
            parsed = parse_nature(nature)
            if parsed and parsed[0] == reported.basis:
                bands.append((parsed[1], record))
    holder = current[0].name
    if not bands:
        return _result(
            "psc",
            "not_comparable",
            f"the PSC entry states no {reported.basis} band (other natures of control only)",
            reported,
            control_as_of=current[0].notified_on,
            matched=holder,
        )
    if len({(b.low, b.high) for b, _ in bands}) > 1:
        labels = ", ".join(sorted({b.label() for b, _ in bands}))
        return _result(
            "psc",
            "not_comparable",
            f"the PSC entries give several {reported.basis} bands ({labels}); none is chosen",
            reported,
            control_as_of=bands[0][1].notified_on,
            matched=holder,
        )
    band, record = bands[0]
    inside = band.contains(reported.percent)
    return _result(
        "psc",
        "agree" if inside else "conflict",
        (
            f"reported {reported.percent}% is inside the PSC {reported.basis} band {band.label()}"
            if inside
            else f"reported {reported.percent}% is outside the PSC {reported.basis} band "
            f"{band.label()}; both are kept for review"
        ),
        reported,
        band=band,
        control_as_of=record.notified_on,
        matched=record.name,
    )


def compare_gleif(
    reported: ReportedStake, parent: GleifParent | None, as_of: dt.datetime
) -> ControlComparison:
    """Compare a reported stake with the GLEIF accounting-consolidation parent. Consolidation
    implies control, usually a majority; a minority stake says nothing about it."""
    if parent is None or parent.known_at > as_of:
        return _result(
            "gleif", "not_comparable", "no GLEIF parent record is known for the issuer", reported
        )
    if parent.parent_lei is None:
        why = parent.reporting_exception or "no parent and no reporting exception"
        return _result(
            "gleif",
            "not_comparable",
            f"GLEIF states no consolidating parent ({why})",
            reported,
        )
    if reported.percent is None:
        return _result("gleif", "not_comparable", "the reported stake states no percent", reported)
    is_parent = (
        reported.holder_lei == parent.parent_lei
        if reported.holder_lei
        else bool(parent.parent_name)
        and normalise_name(reported.holder_name) == normalise_name(parent.parent_name or "")
    )
    via = "LEI" if reported.holder_lei else "name only"
    majority = reported.percent > Decimal(50)
    if is_parent and majority:
        return _result(
            "gleif",
            "agree",
            f"the reported holder is the GLEIF {parent.relationship or 'consolidation'} parent "
            f"({via}) and reports a majority of {reported.percent}%",
            reported,
            matched=parent.parent_name,
        )
    if is_parent:
        return _result(
            "gleif",
            "conflict",
            f"GLEIF names the reported holder as consolidating parent ({via}) but the stake is "
            f"{reported.percent}%, not a majority; control may rest on other rights. "
            "Kept for review",
            reported,
            matched=parent.parent_name,
        )
    if majority:
        return _result(
            "gleif",
            "conflict",
            f"the reported holder states {reported.percent}% but GLEIF names "
            f"{parent.parent_name or parent.parent_lei} as consolidating parent. Kept for review",
            reported,
            matched=parent.parent_name,
        )
    return _result(
        "gleif",
        "not_comparable",
        "a minority stake held by someone other than the GLEIF parent does not bear on "
        "consolidation",
        reported,
        matched=parent.parent_name,
    )


def corroborate(
    reported: ReportedStake,
    pscs: list[PscRecord],
    parent: GleifParent | None,
    as_of: dt.datetime,
) -> list[ControlComparison]:
    """Both comparisons side by side. They are not merged: a PSC agreement and a GLEIF conflict
    are two facts, and no combined verdict is produced."""
    return [compare_psc(reported, pscs, as_of), compare_gleif(reported, parent, as_of)]
