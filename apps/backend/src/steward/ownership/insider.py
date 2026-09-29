"""SEC Forms 3, 4 and 5 ownership filings and their reported security rows."""

import datetime as dt
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

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


@dataclass(frozen=True)
class InsiderFiling:
    issuer_cik: str
    issuer_name: str | None
    issuer_ticker: str | None
    reporting_period: dt.date | None
    rows: list[InsiderRow]


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
                    shares=_number(item, "transactionAmounts", "transactionShares", "value")
                    or _number(item, "underlyingSecurity", "underlyingSecurityShares", "value")
                    or _number(item, "sharesOwnedFollowingTransaction", "value"),
                    price_per_share=_number(
                        item, "transactionAmounts", "transactionPricePerShare", "value"
                    ),
                    acquired_disposed=_text(
                        item, "transactionAmounts", "transactionAcquiredDisposedCode", "value"
                    ),
                    shares_owned_after=_number(
                        item, "postTransactionAmounts", "sharesOwnedFollowingTransaction", "value"
                    )
                    or _number(item, "sharesOwnedFollowingTransaction", "value"),
                    direct_indirect=_text(
                        item, "ownershipNature", "directOrIndirectOwnership", "value"
                    ),
                    derivative=derivative,
                )
            )
    return InsiderFiling(
        issuer_cik=issuer_cik,
        issuer_name=issuer_name,
        issuer_ticker=issuer_ticker.upper() if issuer_ticker else None,
        reporting_period=_date(root, "periodOfReport"),
        rows=rows,
    )
