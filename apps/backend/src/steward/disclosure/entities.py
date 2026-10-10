"""P5.4: resolve a counterparty mention to a known issuer, without guessing.

A mention resolves automatically only through an identifier (CIK or LEI). A name, a dated alias or
a former name can find CANDIDATES, but a candidate is a suggestion: a name-only match needs a
reviewer, and two issuers sharing a name or brand are `ambiguous`, never silently picked. An
anonymous label such as "Customer A" is never resolved at all.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

_SUFFIXES = {
    "inc",
    "incorporated",
    "corp",
    "corporation",
    "co",
    "company",
    "ltd",
    "limited",
    "plc",
    "llc",
    "lp",
    "sa",
    "ag",
    "nv",
    "se",
    "gmbh",
    "the",
}
_ANONYMOUS = re.compile(r"^(customer|supplier|distributor|partner|client)\s+[a-z0-9]$", re.I)


def norm(name: str) -> str:
    """Case, punctuation and legal-form suffixes removed: 'Apple Inc.' and 'APPLE, INC' agree."""
    words = re.sub(r"[^a-z0-9 ]+", " ", name.lower().replace("&", " and ")).split()
    kept = [w for w in words if w not in _SUFFIXES]
    return " ".join(kept or words)


@dataclass(frozen=True)
class IssuerRef:
    issuer_id: int
    legal_name: str
    cik: str | None = None
    lei: str | None = None
    ticker: str | None = None  # the held ticker, when this issuer is one the owner holds


@dataclass(frozen=True)
class Alias:
    issuer_id: int
    name: str
    kind: str  # legal | former | trading | translated
    valid_from: dt.date | None = None
    valid_to: dt.date | None = None

    def valid_on(self, day: dt.date) -> bool:
        return (self.valid_from is None or self.valid_from <= day) and (
            self.valid_to is None or day <= self.valid_to
        )


Status = Literal["matched", "candidate", "ambiguous", "none", "anonymous"]
Basis = Literal["cik", "lei", "registered_name", "alias"]


@dataclass(frozen=True)
class Resolution:
    status: Status
    issuer_ids: tuple[int, ...] = ()
    basis: Basis | None = None
    needs_review: bool = False
    note: str = ""


def resolve(
    mention: str | None,
    issuers: Iterable[IssuerRef],
    aliases: Iterable[Alias],
    on: dt.date,
    *,
    cik: str | None = None,
    lei: str | None = None,
) -> Resolution:
    """One of: `matched` (an identifier agrees), `candidate` (one name match, needs review),
    `ambiguous` (several issuers fit, needs review), `none`, or `anonymous` (never resolved)."""
    people = list(issuers)
    if cik:
        hit = [i for i in people if i.cik and i.cik.lstrip("0") == cik.lstrip("0")]
        if len(hit) == 1:
            return Resolution("matched", (hit[0].issuer_id,), "cik")
    if lei:
        hit = [i for i in people if i.lei and i.lei.upper() == lei.upper()]
        if len(hit) == 1:
            return Resolution("matched", (hit[0].issuer_id,), "lei")
    if not mention or not mention.strip():
        return Resolution("none", note="no name or identifier to match")
    if _ANONYMOUS.match(mention.strip()):
        return Resolution("anonymous", note="an anonymous label is never resolved to an entity")
    wanted = norm(mention)
    by_name = sorted({i.issuer_id for i in people if norm(i.legal_name) == wanted})
    if len(by_name) == 1:
        return Resolution(
            "candidate",
            tuple(by_name),
            "registered_name",
            True,
            "a name match alone is a suggestion until a reviewer confirms it",
        )
    if len(by_name) > 1:
        return Resolution(
            "ambiguous",
            tuple(by_name),
            "registered_name",
            True,
            "several issuers share this name: a reviewer must choose",
        )
    by_alias = sorted({a.issuer_id for a in aliases if a.valid_on(on) and norm(a.name) == wanted})
    if len(by_alias) == 1:
        return Resolution(
            "candidate",
            tuple(by_alias),
            "alias",
            True,
            "matched a dated alias; a reviewer must confirm it is the same entity",
        )
    if len(by_alias) > 1:
        return Resolution(
            "ambiguous",
            tuple(by_alias),
            "alias",
            True,
            "the alias belonged to more than one issuer on that date",
        )
    return Resolution("none", note="no issuer or alias matches this name")


def key_for(issuer: IssuerRef) -> str:
    """The key a scenario uses for an entity: the held ticker when there is one, else the CIK or
    LEI, else the issuer id. Two reports about the same issuer therefore meet at one node."""
    if issuer.ticker:
        return issuer.ticker
    if issuer.cik:
        return f"cik:{issuer.cik.lstrip('0')}"
    if issuer.lei:
        return f"lei:{issuer.lei.upper()}"
    return f"issuer:{issuer.issuer_id}"
