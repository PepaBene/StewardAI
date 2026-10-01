"""SEC Forms 3, 4 and 5 ownership filings and their reported security rows."""

import datetime as dt
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from steward.ownership.insider_classify import classify, plan_flag
from steward.sources.edgar import Filing


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child(node: ET.Element, name: str) -> ET.Element | None:
    return next((child for child in node if _local(child.tag) == name), None)


def _path(node: ET.Element | None, *names: str) -> ET.Element | None:
    for name in names:
        node = _child(node, name) if node is not None else None
    return node


def _text(node: ET.Element | None, *names: str) -> str | None:
    found = _path(node, *names)
    value = found.text.strip() if found is not None and found.text else None
    return value or None


def _number(node: ET.Element | None, *names: str) -> Decimal | None:
    raw = _text(node, *names)
    if raw is None:
        return None
    try:
        value = Decimal(raw)
    except InvalidOperation:
        return None
    return value if value.is_finite() else None


def _date(node: ET.Element | None, *names: str) -> dt.date | None:
    raw = _text(node, *names)
    try:
        return dt.date.fromisoformat(raw) if raw else None
    except ValueError:
        return None


def _truth(node: ET.Element, name: str) -> bool:
    return (_text(node, name) or "").lower() in {"1", "true", "yes"}


@dataclass(frozen=True)
class InsiderRow:
    reporting_owner_cik: str
    owner_name: str | None
    officer_title: str | None
    is_director: bool
    is_officer: bool
    is_ten_percent_owner: bool
    security_title: str | None
    transaction_date: dt.date | None
    transaction_code: str
    shares: Decimal | None
    price_per_share: Decimal | None
    acquired_disposed: str | None
    shares_owned_after: Decimal | None
    direct_indirect: str | None
    derivative: bool
    # Enrichment (D6.7): footnotes, derivative terms, Rule 10b5-1, economic classification.
    footnote_ids: tuple[str, ...] = ()
    footnote_text: str | None = None
    exercise_price: Decimal | None = None
    exercisable_date: dt.date | None = None
    expiration_date: dt.date | None = None
    underlying_security_title: str | None = None
    underlying_shares: Decimal | None = None
    deemed_execution_date: dt.date | None = None
    equity_swap_involved: bool | None = None
    economic_class: str | None = None
    classification_basis: str | None = None
    rule_10b5_1_plan: bool | None = None
    rule_10b5_1_basis: str | None = None
    rule_10b5_1_adopted: dt.date | None = None


@dataclass(frozen=True)
class InsiderFiling:
    issuer_cik: str
    issuer_name: str | None
    issuer_ticker: str | None
    reporting_period: dt.date | None
    rows: list[InsiderRow]
    document_type: str | None = None
    is_amendment: bool = False
    original_submission_date: dt.date | None = None
    reporting_owner_cik: str | None = None
    joint_owner_count: int = 1
    aff10b5_one: bool | None = None
    footnotes: dict[str, str] | None = None
    remarks: str | None = None


def _first[T](*values: T | None) -> T | None:
    """The first value that is not None (0 is a real value, unlike with `or`)."""
    return next((v for v in values if v is not None), None)


def _flag(node: ET.Element | None, *names: str) -> bool | None:
    raw = _text(node, *names)
    if raw is None:
        return None
    if raw.lower() in {"1", "true", "yes"}:
        return True
    if raw.lower() in {"0", "false", "no"}:
        return False
    return None


def _footnote_ids(item: ET.Element) -> tuple[str, ...]:
    ids = [
        node.attrib["id"]
        for node in item.iter()
        if _local(node.tag) == "footnoteId" and node.attrib.get("id")
    ]
    return tuple(dict.fromkeys(ids))


def parse_ownership_xml(xml: bytes, filing: Filing) -> InsiderFiling:
    """Parse as-filed ownership XML, keeping transactions and initial holdings."""
    if b"<!DOCTYPE" in xml.upper() or b"<!ENTITY" in xml.upper():
        raise ValueError("ownership XML with DTD or entity declarations is not accepted")
    root = ET.fromstring(xml)  # noqa: S314 - DTD/entity declarations are rejected above.
    issuer = _child(root, "issuer")
    issuer_cik = (_text(issuer, "issuerCik") or filing.cik).zfill(10)
    issuer_name = _text(issuer, "issuerName")
    issuer_ticker = _text(issuer, "issuerTradingSymbol")
    rows: list[InsiderRow] = []

    owners = [node for node in root.iter() if _local(node.tag) == "reportingOwner"]
    owner = owners[0] if owners else None
    owner_id = _path(owner, "reportingOwnerId")
    relationship = _path(owner, "reportingOwnerRelationship")
    owner_cik = (_text(owner_id, "rptOwnerCik") or "").zfill(10)
    owner_name = _text(owner_id, "rptOwnerName")
    director = _truth(relationship, "isDirector") if relationship is not None else False
    officer = _truth(relationship, "isOfficer") if relationship is not None else False
    ten_percent = _truth(relationship, "isTenPercentOwner") if relationship is not None else False
    title = _text(relationship, "officerTitle")
    document_type = _text(root, "documentType")
    footnotes = {
        node.attrib["id"]: " ".join("".join(node.itertext()).split())
        for node in root.iter()
        if _local(node.tag) == "footnote" and node.attrib.get("id")
    }
    checkbox = _flag(root, "aff10b5One")

    for table_name, derivative in (("nonDerivativeTable", False), ("derivativeTable", True)):
        table = _child(root, table_name)
        if table is None:
            continue
        transactions = [
            node
            for node in table
            if _local(node.tag) in {"nonDerivativeTransaction", "derivativeTransaction"}
        ]
        holdings = [
            node
            for node in table
            if _local(node.tag) in {"nonDerivativeHolding", "derivativeHolding"}
        ]
        for index, item in enumerate([*transactions, *holdings]):
            code = _text(item, "transactionCoding", "transactionCode") or (
                "INITIAL_HOLDING" if index >= len(transactions) else "OTHER"
            )
            ids = _footnote_ids(item)
            footnote_text = " | ".join(footnotes[i] for i in ids if footnotes.get(i)) or None
            classification = classify(code, derivative=derivative, footnote_text=footnote_text)
            plan = plan_flag(footnote_text, checkbox)
            rows.append(
                InsiderRow(
                    reporting_owner_cik=owner_cik,
                    owner_name=owner_name,
                    officer_title=title,
                    is_director=director,
                    is_officer=officer,
                    is_ten_percent_owner=ten_percent,
                    security_title=_text(item, "securityTitle", "value"),
                    transaction_date=_date(item, "transactionDate", "value"),
                    transaction_code=code,
                    shares=_first(
                        _number(item, "transactionAmounts", "transactionShares", "value"),
                        _number(item, "underlyingSecurity", "underlyingSecurityShares", "value"),
                        _number(item, "sharesOwnedFollowingTransaction", "value"),
                    ),
                    price_per_share=_number(
                        item, "transactionAmounts", "transactionPricePerShare", "value"
                    ),
                    acquired_disposed=_text(
                        item, "transactionAmounts", "transactionAcquiredDisposedCode", "value"
                    ),
                    shares_owned_after=_first(
                        _number(
                            item,
                            "postTransactionAmounts",
                            "sharesOwnedFollowingTransaction",
                            "value",
                        ),
                        _number(item, "sharesOwnedFollowingTransaction", "value"),
                    ),
                    direct_indirect=_text(
                        item, "ownershipNature", "directOrIndirectOwnership", "value"
                    ),
                    derivative=derivative,
                    footnote_ids=ids,
                    footnote_text=footnote_text,
                    exercise_price=_number(item, "conversionOrExercisePrice", "value"),
                    exercisable_date=_date(item, "exerciseDate", "value"),
                    expiration_date=_date(item, "expirationDate", "value"),
                    underlying_security_title=_text(
                        item, "underlyingSecurity", "underlyingSecurityTitle", "value"
                    ),
                    underlying_shares=_number(
                        item, "underlyingSecurity", "underlyingSecurityShares", "value"
                    ),
                    deemed_execution_date=_date(item, "deemedExecutionDate", "value"),
                    equity_swap_involved=_flag(item, "equitySwapInvolved"),
                    economic_class=classification.economic_class,
                    classification_basis=classification.basis,
                    rule_10b5_1_plan=plan.plan,
                    rule_10b5_1_basis=plan.basis,
                    rule_10b5_1_adopted=plan.adopted,
                )
            )
    return InsiderFiling(
        issuer_cik=issuer_cik,
        issuer_name=issuer_name,
        issuer_ticker=issuer_ticker.upper() if issuer_ticker else None,
        reporting_period=_date(root, "periodOfReport"),
        rows=rows,
        document_type=document_type,
        is_amendment=bool((document_type or filing.form).endswith("/A")),
        original_submission_date=_date(root, "dateOfOriginalSubmission"),
        reporting_owner_cik=owner_cik if owner_cik.strip("0") else None,
        joint_owner_count=max(len(owners), 1),
        aff10b5_one=checkbox,
        footnotes=footnotes or None,
        remarks=_text(root, "remarks"),
    )


# ---------------------------------------------------------------------------
# Amendments: which earlier filing a Form 3/4/5 /A restates
# ---------------------------------------------------------------------------

RowKey = tuple[dt.date | None, str, str | None]


@dataclass(frozen=True)
class OriginalCandidate:
    accession: str
    filed: dt.date
    keys: frozenset[RowKey]


def match_original(
    candidates: list[OriginalCandidate],
    amendment_keys: frozenset[RowKey],
    original_submission_date: dt.date | None,
) -> tuple[str | None, str]:
    """The original an amendment restates, and the link status.

    An amendment restates one Form 3/4/5, but an insider often files several Form 4s for one
    period. The amendment names the original's filing date; when that leaves more than one
    candidate, the one sharing the most (date, code, security) rows wins if it is unique.
    Otherwise the link is `ambiguous` and nothing is superseded: guessing would drop real
    transactions from the net activity.
    """
    pool = candidates
    if original_submission_date is not None:
        dated = [c for c in candidates if c.filed == original_submission_date]
        pool = dated or candidates
    if not pool:
        return None, "original_not_found"
    if len(pool) == 1:
        return pool[0].accession, "linked"
    scored = sorted(((len(c.keys & amendment_keys), c.accession) for c in pool), reverse=True)
    if scored[0][0] > 0 and (len(scored) == 1 or scored[0][0] > scored[1][0]):
        return scored[0][1], "linked"
    return None, "ambiguous"
